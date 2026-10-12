# 第 2 章：多模型适配层

## 核心概念

在第 1 章中，我们的代码直接使用了 Anthropic 的 API。但现实中，你可能需要：

- **切换模型厂商**：从 Claude 换到 GPT，或者对比两者的效果
- **统一消息格式**：不同厂商的 API 差异很大（OpenAI 用 `tool_calls`，Anthropic 用 `tool_use`）
- **流式输出**：边生成边显示，提升用户体验
- **会话中途切换**：同一个对话里换个模型试试

这一章我们构建一个**多模型适配层**（provider abstraction），让上层代码无需关心底层是哪家厂商。

```
┌─────────────────────┐
│   Agent 主循环      │  ← 统一接口，不关心厂商
└──────────┬──────────┘
           │
    ┌──────▼───────┐
    │   Provider   │  ← 抽象层
    │   接口       │
    └──────┬───────┘
           │
    ┏━━━━━━┻━━━━━━┓
    ┃              ┃
┌───▼───┐     ┌───▼────┐
│OpenAI │     │Anthropic│  ← 具体实现
│Adapter│     │ Adapter │
└───────┘     └─────────┘
```

## 核心挑战：两家厂商的 API 差异

### 1. 工具调用格式完全不同

**OpenAI 格式**：

```json
{
  "role": "assistant",
  "content": "我来帮你执行命令",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "bash",
        "arguments": "{\"command\": \"ls\"}"
      }
    }
  ]
}
```

**Anthropic 格式**：

```json
{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "我来帮你执行命令"
    },
    {
      "type": "tool_use",
      "id": "toolu_abc123",
      "name": "bash",
      "input": {"command": "ls"}
    }
  ]
}
```

**关键差异**：
- OpenAI：`tool_calls` 是独立字段，`arguments` 是 JSON 字符串
- Anthropic：`content` 是混合数组（文本 + 工具调用），`input` 是直接的对象

### 2. 工具结果返回方式不同

**OpenAI 格式**：

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "main.py\nREADME.md"
}
```

**Anthropic 格式**：

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_abc123",
      "content": "main.py\nREADME.md"
    }
  ]
}
```

**关键差异**：
- OpenAI：工具结果放在 `role: "tool"` 消息中
- Anthropic：工具结果放在 `role: "user"` 消息的 `content` 数组中

### 3. 流式响应的事件格式不同

**OpenAI 的事件**：

```python
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end='')
    if delta.tool_calls:
        # 工具调用是增量的（每次只有一小块）
        for tc_delta in delta.tool_calls:
            # 需要手动累积
```

**Anthropic 的事件**：

```python
for event in stream:
    if event.type == "content_block_delta":
        if event.delta.type == "text_delta":
            print(event.delta.text, end='')
```

## 我们的实现（Python）

### 文件结构

```
02-providers/
├── src/
│   ├── main.py           # 主程序 + 统一接口
│   ├── openai_adapter.py # OpenAI 适配器
│   ├── anthropic_adapter.py # Anthropic 适配器
│   ├── test_main.py      # 单元测试
│   └── test_adapters.py  # 适配器测试
├── requirements.txt
└── README.md
```

### 核心代码解析

#### 1. 统一的消息格式（main.py）

我们定义一套**标准消息格式**，所有适配器都转换成这个格式：

```python
@dataclass
class ToolCall:
    """统一的工具调用格式"""
    id: str
    name: str
    arguments: dict  # 统一用字典，不是字符串

@dataclass
class Message:
    """统一的消息格式"""
    role: str  # "user" | "assistant" | "tool"
    content: str | None
    tool_calls: list[ToolCall] | None
    tool_call_id: str | None  # 当 role == "tool" 时使用
```

**设计原则**：
- **扁平化**：不用嵌套的 `content` 数组，工具调用单独放在 `tool_calls` 字段
- **统一类型**：`arguments` 统一是字典，避免 JSON 字符串解析
- **简单映射**：可以轻松转换成任何厂商的格式

#### 2. Provider 接口（main.py）

```python
class Provider(ABC):
    """多模型适配层的抽象接口"""
    
    @abstractmethod
    def create_completion(
        self,
        messages: list[Message],
        tools: list[dict],
        stream: bool = False
    ) -> Message | Iterator[dict]:
        """
        发送请求，返回 Message 或流式事件
        
        Args:
            messages: 统一格式的消息历史
            tools: 工具定义（JSON Schema 格式）
            stream: 是否流式输出
            
        Returns:
            如果 stream=False，返回完整的 Message
            如果 stream=True，返回事件迭代器
        """
        pass
    
    @abstractmethod
    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        """将统一格式转换为厂商原生格式"""
        pass
    
    @abstractmethod
    def from_native_message(self, native_msg: Any) -> Message:
        """将厂商原生格式转换为统一格式"""
        pass
```

#### 3. OpenAI 适配器（openai_adapter.py）

完整实现见源码。关键点：

```python
def to_native_messages(self, messages: list[Message]) -> list[dict]:
    """将统一格式转换为 OpenAI 格式"""
    native = []
    for msg in messages:
        if msg.role == "tool":
            # OpenAI: 工具结果单独用 role="tool"
            native.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content
            })
        elif msg.tool_calls:
            # OpenAI: tool_calls 是独立字段
            native.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in msg.tool_calls
                ]
            })
        else:
            native.append({"role": msg.role, "content": msg.content or ""})
    return native
```

**关键转换**：
- 工具调用 → `tool_calls` 数组，`arguments` 序列化成 JSON 字符串
- 工具结果 → `role: "tool"` 消息

#### 4. Anthropic 适配器（anthropic_adapter.py）

```python
def to_native_messages(self, messages: list[Message]) -> list[dict]:
    """将统一格式转换为 Anthropic 格式"""
    native = []
    for msg in messages:
        if msg.role == "tool":
            # Anthropic: 工具结果放在 user 消息的 content 数组中
            native.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content
                    }
                ]
            })
        elif msg.tool_calls:
            # Anthropic: content 是混合数组（文本 + 工具调用）
            content = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments
                })
            native.append({"role": "assistant", "content": content})
        else:
            native.append({"role": msg.role, "content": msg.content or ""})
    return native
```

**关键转换**：
- 工具调用 → `content` 数组中的 `tool_use` 块
- 工具结果 → `user` 消息，`content` 里包 `tool_result`

#### 5. 流式输出（openai_adapter.py）

```python
def create_completion(self, messages, tools, stream=False):
    native_messages = self.to_native_messages(messages)
    
    if stream:
        # 流式模式
        response = self.client.chat.completions.create(
            model=self.model,
            messages=native_messages,
            tools=self._convert_tools(tools),
            stream=True
        )
        
        # 累积流式片段
        accumulated = {
            "content": "",
            "tool_calls": {}  # {index: {name, arguments}}
        }
        
        for chunk in response:
            delta = chunk.choices[0].delta
            
            # 文本内容
            if delta.content:
                accumulated["content"] += delta.content
                yield {"type": "text_delta", "text": delta.content}
            
            # 工具调用（需要累积，因为是增量的）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    index = tc_delta.index
                    if index not in accumulated["tool_calls"]:
                        accumulated["tool_calls"][index] = {
                            "id": tc_delta.id,
                            "name": "",
                            "arguments": ""
                        }
                    
                    if tc_delta.function.name:
                        accumulated["tool_calls"][index]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        accumulated["tool_calls"][index]["arguments"] += tc_delta.function.arguments
                    
                    yield {"type": "tool_call_delta", "index": index}
        
        # 流式结束，返回完整消息
        yield {"type": "done", "message": self._build_message(accumulated)}
    else:
        # 非流式模式（同第1章）
        response = self.client.chat.completions.create(...)
        return self.from_native_message(response.choices[0].message)
```

**流式输出的难点**：
- **增量累积**：OpenAI 的 `tool_calls` 是一块块发的（函数名、参数都是增量），需要手动拼接
- **事件类型**：我们定义了 `text_delta`、`tool_call_delta`、`done` 三种事件
- **最终返回**：流式结束时，返回完整的 `Message` 对象

## 对比 pi 的真实实现（TypeScript）

### 1. Provider 接口定义（packages/ai/src/provider.ts）

**pi 的接口**（简化版）：

```typescript
export interface LlmProvider {
  streamCompletion(
    model: string,
    context: LlmRequestContext,
    options: LlmRequestOptions,
  ): AsyncIterable<LlmChunk>;
  
  createCompletion(
    model: string,
    context: LlmRequestContext,
    options: LlmRequestOptions,
  ): Promise<AssistantMessage>;
}
```

**TypeScript 语法说明**：
- `interface`：接口定义（类似 Python 的 `Protocol` 或抽象类）
- `AsyncIterable<T>`：异步可迭代对象（类似 Python 的 `AsyncIterator[T]`）
- `Promise<T>`：异步操作返回值（类似 Python 的 `Awaitable[T]`）

**对比我们的实现**：
- pi 把流式和非流式分成两个方法，我们用一个方法 + `stream` 参数
- pi 的 `LlmRequestContext` 包含更多元信息（token 预算、中止信号等）

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/provider.ts#L50-L70

### 2. 消息标准化（packages/ai/src/messages.ts）

**pi 的标准格式**：

```typescript
export type AgentMessage = 
  | UserMessage
  | AssistantMessage
  | ToolResultMessage;

export interface AssistantMessage {
  role: "assistant";
  content: MessageContent[];  // 内容块数组
  stopReason?: "tool_use" | "end_turn" | "length" | "error";
  thinking?: string;  // Claude 的思考过程（如果启用了）
}

export type MessageContent = 
  | TextContent
  | ToolUseContent;

export interface ToolUseContent {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
}
```

**TypeScript 语法说明**：
- `type A = B | C`：联合类型，A 可以是 B 或 C（类似 Python 的 `Union[B, C]`）
- `interface { field?: Type }`：可选字段（类似 Python 的 `field: Type | None`）
- `Record<K, V>`：键值对映射（类似 Python 的 `dict[K, V]`）

**对比我们的实现**：
- pi 保留了 Anthropic 风格的 `content` 数组（更灵活，可以混合文本和工具调用）
- 我们扁平化成 `content` + `tool_calls` 两个字段（更简单，但牺牲了灵活性）
- pi 记录了 `stopReason`，可以区分"调用工具"和"自然结束"

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/messages.ts#L20-L50

### 3. OpenAI 适配器（packages/ai/src/providers/openai.ts）

**pi 的转换逻辑**（简化版）：

```typescript
function toOpenAiMessages(messages: AgentMessage[]): OpenAI.ChatCompletionMessageParam[] {
  const result: OpenAI.ChatCompletionMessageParam[] = [];
  
  for (const msg of messages) {
    if (msg.role === "assistant") {
      const toolCalls = msg.content
        .filter((c): c is ToolUseContent => c.type === "tool_use")
        .map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: {
            name: tc.name,
            arguments: JSON.stringify(tc.input),
          },
        }));
      
      result.push({
        role: "assistant",
        content: extractText(msg.content),
        tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
      });
    }
    // ... 其他角色的转换
  }
  
  return result;
}
```

**TypeScript 语法说明**：
- `.filter((c): c is Type => ...)` ：类型守卫（type guard），过滤后确保类型是 `Type`
- `as const`：字面量类型断言，`"function"` 被推断为字面量类型而非 `string`
- `?: Type`：可选属性

**关键处理**：
- **提取文本**：从 `content` 数组中提取所有文本块并拼接
- **工具调用转换**：筛选出 `tool_use` 块，转成 OpenAI 的 `tool_calls` 格式
- **arguments 序列化**：`JSON.stringify(tc.input)`，和我们一样

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/providers/openai.ts#L150-L200

### 4. 流式解析的增量累积（packages/ai/src/providers/openai.ts）

**pi 的流式处理**（简化版）：

```typescript
async function* streamOpenAiCompletion(
  client: OpenAI,
  request: OpenAI.ChatCompletionCreateParams,
): AsyncGenerator<LlmChunk> {
  const stream = await client.chat.completions.create({ ...request, stream: true });
  
  const accumulated: PartialMessage = {
    role: "assistant",
    content: [],
    toolCalls: new Map(),  // Map<index, PartialToolCall>
  };
  
  for await (const chunk of stream) {
    const delta = chunk.choices[0]?.delta;
    if (!delta) continue;
    
    // 处理文本增量
    if (delta.content) {
      if (accumulated.textBlock === undefined) {
        accumulated.textBlock = { type: "text", text: "" };
        accumulated.content.push(accumulated.textBlock);
      }
      accumulated.textBlock.text += delta.content;
      yield { type: "content_delta", contentType: "text", delta: delta.content };
    }
    
    // 处理工具调用增量
    if (delta.tool_calls) {
      for (const tcDelta of delta.tool_calls) {
        const index = tcDelta.index;
        
        if (!accumulated.toolCalls.has(index)) {
          accumulated.toolCalls.set(index, {
            id: tcDelta.id || "",
            name: "",
            arguments: "",
          });
        }
        
        const tc = accumulated.toolCalls.get(index)!;
        if (tcDelta.id) tc.id = tcDelta.id;
        if (tcDelta.function?.name) tc.name += tcDelta.function.name;
        if (tcDelta.function?.arguments) tc.arguments += tcDelta.function.arguments;
        
        yield { type: "content_delta", contentType: "tool_call", index };
      }
    }
  }
  
  // 流式结束，解析 arguments 并返回完整消息
  for (const [_, tc] of accumulated.toolCalls) {
    accumulated.content.push({
      type: "tool_use",
      id: tc.id,
      name: tc.name,
      input: JSON.parse(tc.arguments),
    });
  }
  
  yield { type: "message_done", message: accumulated as AssistantMessage };
}
```

**TypeScript 语法说明**：
- `async function*`：异步生成器函数（类似 Python 的 `async def` + `yield`）
- `AsyncGenerator<T>`：异步生成器类型（类似 Python 的 `AsyncIterator[T]`）
- `for await (const x of iter)`：异步迭代（类似 Python 的 `async for x in iter`）
- `Map<K, V>`：映射数据结构（类似 Python 的 `dict[K, V]`）
- `?.`：可选链，如果左边是 null/undefined 则短路返回 undefined
- `!`：非空断言，告诉编译器"我保证这不是 null/undefined"

**关键细节**：
- **Map 而非对象**：用 `Map` 存储工具调用，因为 index 可以不连续
- **懒解析 arguments**：流式时先累积字符串，最后才 `JSON.parse`（避免解析残缺 JSON）
- **content 数组**：pi 的标准格式是 `content` 数组，所以文本和工具调用都 push 进去

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/providers/openai.ts#L250-L350

### 5. Anthropic 的流式处理（packages/ai/src/providers/anthropic.ts）

**pi 的实现**（简化版）：

```typescript
async function* streamAnthropicCompletion(
  client: Anthropic,
  request: Anthropic.MessageCreateParams,
): AsyncGenerator<LlmChunk> {
  const stream = client.messages.stream(request);
  
  for await (const event of stream) {
    switch (event.type) {
      case "content_block_start":
        if (event.content_block.type === "tool_use") {
          yield {
            type: "content_start",
            contentType: "tool_call",
            toolCall: {
              id: event.content_block.id,
              name: event.content_block.name,
              input: {},
            },
          };
        }
        break;
      
      case "content_block_delta":
        if (event.delta.type === "text_delta") {
          yield { type: "content_delta", contentType: "text", delta: event.delta.text };
        } else if (event.delta.type === "input_json_delta") {
          yield { type: "content_delta", contentType: "tool_call", delta: event.delta.partial_json };
        }
        break;
      
      case "message_delta":
        if (event.delta.stop_reason) {
          stopReason = event.delta.stop_reason;
        }
        break;
    }
  }
  
  // Anthropic 的流式 SDK 会自动累积完整消息
  const finalMessage = await stream.finalMessage();
  yield { type: "message_done", message: fromAnthropicMessage(finalMessage) };
}
```

**TypeScript 语法说明**：
- `switch (value)`：多分支条件判断（类似 Python 的 `match-case`）

**Anthropic 的优势**：
- **自动累积**：SDK 的 `stream.finalMessage()` 会自动给你完整消息，不用手动拼接
- **input_json_delta**：工具调用的参数是增量 JSON 片段，直接是对象形式（不是字符串）

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/providers/anthropic.ts#L200-L280

### 6. 消息验证和修复（packages/ai/src/message-validation.ts）

**pi 的额外功能**：在发送给 LLM 前，验证消息数组是否符合规范，如果不符合就自动修复。

```typescript
export function validateAndFixMessages(messages: AgentMessage[]): {
  messages: AgentMessage[];
  warnings: string[];
} {
  const warnings: string[] = [];
  const fixed: AgentMessage[] = [];
  
  // 规则 1: 第一条消息必须是 user
  if (messages[0]?.role !== "user") {
    warnings.push("First message must be from user, inserting placeholder");
    fixed.push({ role: "user", content: [{ type: "text", text: "(session start)" }] });
  }
  
  // 规则 2: user 和 assistant 必须交替
  let lastRole: string | undefined;
  for (const msg of messages) {
    if (msg.role === lastRole && msg.role !== "tool_result") {
      warnings.push(`Duplicate ${msg.role} messages, merging`);
      // 合并到前一条消息
      const prev = fixed[fixed.length - 1];
      prev.content.push(...msg.content);
    } else {
      fixed.push(msg);
    }
    lastRole = msg.role;
  }
  
  // 规则 3: 每个 tool_use 必须有对应的 tool_result
  // ... 检查 ID 匹配
  
  return { messages: fixed, warnings };
}
```

**TypeScript 语法说明**：
- `?:`：可选链，如果 messages[0] 不存在则返回 undefined
- `!==`：严格不等于（类型和值都要比较）
- `...array`：展开运算符，将数组元素展开

**为什么需要验证？**
- **API 限制**：大部分 LLM API 要求消息必须是 user/assistant 交替的
- **工具调用完整性**：如果有 `tool_use` 但没有对应的 `tool_result`，模型会报错
- **自动修复**：有些情况下可以自动修复（比如合并连续的同角色消息）

我们的简化版没有这一层，但生产环境建议加上。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/ai/src/message-validation.ts

## 总结对比表

| 特性 | mini-pi (Python) | pi 真实实现 (TypeScript) |
|---|---|---|
| 统一消息格式 | ✅ 扁平化（`content` + `tool_calls`） | ✅ Anthropic 风格（`content` 数组） |
| OpenAI 支持 | ✅ 完整支持 | ✅ 完整支持 |
| Anthropic 支持 | ✅ 完整支持 | ✅ 完整支持 + 扩展思维模式 |
| 流式输出 | ✅ 支持 | ✅ 支持 + 更细粒度的事件 |
| 消息验证 | ❌ | ✅ 自动修复不合规消息 |
| 更多厂商 | ❌ | ✅ Google Gemini、Ollama 等 |
| Token 预算管理 | ❌ | ✅ 内置计数和截断 |
| 中止信号 | ❌ | ✅ 全链路 AbortSignal |
| 代码行数 | ~400 行 Python | ~2000 行 TypeScript（包含多家厂商）|

## 如何运行

### 前置条件

```bash
# 在项目根目录安装依赖
pip install -r requirements.txt

# 设置 API key（至少设置一个）
export OPENAI_API_KEY="your-openai-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选，默认官方 API
export OPENAI_MODEL="gpt-4o-mini"  # 可选，默认 gpt-4o-mini

export ANTHROPIC_API_KEY="your-anthropic-key"  # 可选
```

### 运行示例（默认 OpenAI）

```bash
python3 02-providers/src/main.py
```

程序会依次演示：
1. **非流式模式**：等待完整响应
2. **流式模式**：实时显示生成过程
3. **切换厂商**：如果设置了 `ANTHROPIC_API_KEY`，会演示切换到 Claude

### 测试（无需 API key）

```bash
# 使用 pytest
pytest 02-providers/src/ -v

# 或直接运行
python3 02-providers/src/test_main.py
python3 02-providers/src/test_adapters.py
```

## 练习

### 练习 1：添加 Gemini 适配器

**目标**：参照 OpenAI 适配器，实现 Google Gemini 的支持。

**提示**：
- Gemini API 使用 `google-generativeai` 包
- 消息格式类似 OpenAI，但工具调用字段叫 `function_call`
- 参考文档：https://ai.google.dev/tutorials/python_quickstart

**验证**：
```python
provider = GeminiAdapter(api_key="...", model="gemini-1.5-flash")
response = provider.create_completion(messages, tools)
```

### 练习 2：实现消息压缩

**目标**：当消息历史超过 N 个时，自动压缩旧消息（保留最近 5 条 + 第一条）。

**提示**：
- 在 `agent_loop` 里检查 `len(messages) > max_messages`
- 保留 `messages[0]`（初始请求）和 `messages[-5:]`（最近 5 条）
- 插入一条 system 消息："... (省略了 X 条历史消息)"

**扩展**：
- 用 LLM 对省略的消息生成摘要，而不是直接删除

## 下一步

现在我们有了统一的多模型接口，可以无缝切换厂商。但还缺少关键组件：

- **文件工具**：只有 bash 还不够精细，需要 `read`、`write`、`edit`
- **并发控制**：多个工具同时修改同一个文件怎么办？
- **输出截断**：文件太大，怎么保护上下文窗口？

**第 3 章**将实现完整的文件工具系统，对标 pi 的 `coding-agent` 内置工具。
