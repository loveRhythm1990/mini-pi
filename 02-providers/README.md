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

## pi 的高级特性实现（深入分析）

mini-pi 的实现涵盖了核心功能，但 pi 还有一些高级特性值得深入了解。以下基于 pi 仓库 commit `7d75a0e3eb273dff753d49c066ea29fbe7b3b6f6` 的真实源码分析。

### 1. 消息转换与修复（Message Transformation & Repair）

pi 在发送消息给 provider 前，通过 `transformMessages` 函数进行多层转换和修复，确保跨provider兼容性和API合规性。

**核心文件**：`packages/ai/src/api/transform-messages.ts`（commit 7d75a0e，234行）

#### 修复案例 1：空内容规范化（Null Content Normalization）

**问题**：手动构建的历史记录或旧会话文件可能包含 `null` 或 `undefined` 的 `content`。

**pi 的修复**（第74行）：

```typescript
const normalizedMessages = messages.map((msg) => 
  (msg.content == null ? { ...msg, content: [] } : msg)
);
```

**TypeScript 语法说明**：
- `msg.content == null`：使用宽松相等，同时检查 null 和 undefined
- `{ ...msg, content: [] }`：对象展开运算符，复制所有属性并替换 content

**Python 实现**：
```python
def normalize_content(messages: list[Message]) -> list[Message]:
    return [
        Message(**{**msg.__dict__, 'content': msg.content or []})
        if msg.content is None else msg
        for msg in messages
    ]
```

#### 修复案例 2：非视觉模型的图像降级（Image Downgrade for Non-Vision Models）

**问题**：向不支持图像的模型发送图像内容会导致 API 错误。

**pi 的修复**（第35-56行）：

```typescript
function downgradeUnsupportedImages<TApi extends Api>(messages: Message[], model: Model<TApi>): Message[] {
  if (model.input.includes("image")) {
    return messages;  // 模型支持图像，不需要处理
  }
  
  return messages.map((msg) => {
    if (msg.role === "user" && Array.isArray(msg.content)) {
      return {
        ...msg,
        content: replaceImagesWithPlaceholder(
          msg.content, 
          "(image omitted: model does not support images)"
        ),
      };
    }
    
    if (msg.role === "toolResult") {
      return {
        ...msg,
        content: replaceImagesWithPlaceholder(
          msg.content,
          "(tool image omitted: model does not support images)"
        ),
      };
    }
    
    return msg;
  });
}
```

**关键逻辑**：
- 检查 `model.input.includes("image")`，如果支持则跳过
- 替换用户消息和工具结果中的图像为占位文本
- 合并连续的占位符，避免重复

**Python 实现**：
```python
def downgrade_images(messages: list[Message], model_supports_images: bool) -> list[Message]:
    if model_supports_images:
        return messages
    
    result = []
    for msg in messages:
        if msg.role in ("user", "tool") and isinstance(msg.content, list):
            # 替换图像块为文本占位符
            new_content = []
            for block in msg.content:
                if block.get('type') == 'image':
                    new_content.append({
                        'type': 'text',
                        'text': '(image omitted: model does not support images)'
                    })
                else:
                    new_content.append(block)
            msg = Message(**{**msg.__dict__, 'content': new_content})
        result.append(msg)
    return result
```

#### 修复案例 3：Thinking Blocks 跨模型转换

**问题**：不同模型的 thinking/reasoning 格式不兼容（如 OpenAI 的加密推理、Anthropic 的明文思考）。

**pi 的修复**（第100-118行）：

```typescript
const transformedContent = assistantMsg.content.flatMap((block) => {
  if (block.type === "thinking") {
    // 加密的 thinking（redacted）只对同一模型有效
    if (block.redacted) {
      return isSameModel ? block : [];  // 跨模型时丢弃
    }
    
    // 同模型且有签名：保留（用于重放）
    if (isSameModel && block.thinkingSignature) return block;
    
    // 空的 thinking：跳过
    if (!block.thinking || block.thinking.trim() === "") return [];
    
    // 跨模型：转换为普通文本
    if (isSameModel) return block;
    return { type: "text" as const, text: block.thinking };
  }
  // ... 其他类型
});
```

**TypeScript 语法说明**：
- `.flatMap()`：映射后展平，返回 `[]` 会被过滤掉
- `as const`：类型断言为字面量类型

**关键策略**：
1. **加密 thinking**：只在同模型间保留
2. **空 thinking**：直接丢弃
3. **明文 thinking**：跨模型时转为普通文本

**Python 实现**：
```python
def transform_thinking_blocks(
    content: list[dict], 
    is_same_model: bool
) -> list[dict]:
    result = []
    for block in content:
        if block.get('type') == 'thinking':
            if block.get('redacted') and not is_same_model:
                continue  # 跨模型丢弃加密thinking
            if not block.get('thinking', '').strip():
                continue  # 跳过空thinking
            if not is_same_model:
                # 跨模型转为文本
                result.append({'type': 'text', 'text': block['thinking']})
            else:
                result.append(block)
        else:
            result.append(block)
    return result
```

#### 修复案例 4：Tool Call ID 规范化（Cross-Provider ID Normalization）

**问题**：OpenAI 生成的 tool call ID 长达 450+ 字符且包含特殊字符（如 `|`），而 Anthropic 要求 ID 匹配 `^[a-zA-Z0-9_-]+$` 且最多 64 字符。

**pi 的修复**（第60-142行）：

```typescript
export function transformMessages<TApi extends Api>(
  messages: Message[],
  model: Model<TApi>,
  normalizeToolCallId?: (id: string, model: Model<TApi>, source: AssistantMessage) => string,
): Message[] {
  const toolCallIdMap = new Map<string, string>();
  
  // 第一遍：转换 tool call ID
  const transformed = imageAwareMessages.map((msg) => {
    if (msg.role === "assistant") {
      const transformedContent = assistantMsg.content.flatMap((block) => {
        if (block.type === "toolCall") {
          let normalizedToolCall: ToolCall = toolCall;
          
          if (!isSameModel && normalizeToolCallId) {
            const normalizedId = normalizeToolCallId(toolCall.id, model, assistantMsg);
            if (normalizedId !== toolCall.id) {
              toolCallIdMap.set(toolCall.id, normalizedId);  // 记录映射
              normalizedToolCall = { ...normalizedToolCall, id: normalizedId };
            }
          }
          
          return normalizedToolCall;
        }
        return block;
      });
    }
    
    // 同步更新 toolResult 的 toolCallId
    if (msg.role === "toolResult") {
      const normalizedId = toolCallIdMap.get(msg.toolCallId);
      if (normalizedId && normalizedId !== msg.toolCallId) {
        return { ...msg, toolCallId: normalizedId };
      }
    }
    
    return msg;
  });
}
```

**关键设计**：
- 使用 `Map` 记录 ID 映射关系
- 先转换 assistant 消息中的 tool call ID
- 再同步更新对应的 tool result 中的 `toolCallId`

**Python 实现**：
```python
def normalize_tool_call_ids(
    messages: list[Message],
    normalize_fn: callable
) -> list[Message]:
    id_map = {}
    result = []
    
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            new_calls = []
            for tc in msg.tool_calls:
                new_id = normalize_fn(tc.id)
                if new_id != tc.id:
                    id_map[tc.id] = new_id
                    new_calls.append(ToolCall(**{**tc.__dict__, 'id': new_id}))
                else:
                    new_calls.append(tc)
            msg = Message(**{**msg.__dict__, 'tool_calls': new_calls})
        elif msg.role == "tool" and msg.tool_call_id in id_map:
            # 同步更新 tool result
            msg = Message(**{**msg.__dict__, 'tool_call_id': id_map[msg.tool_call_id]})
        result.append(msg)
    
    return result
```

#### 修复案例 5：孤立 Tool Calls 的合成结果（Synthetic Results for Orphaned Tool Calls）

**问题**：assistant 请求了工具调用，但后续消息中缺少对应的 tool result（可能因为中断、错误等）。

**pi 的修复**（第158-233行）：

```typescript
const result: Message[] = [];
let pendingToolCalls: ToolCall[] = [];
let existingToolResultIds = new Set<string>();

const closePendingToolCalls = () => {
  if (pendingToolCalls.length > 0) {
    for (const tc of pendingToolCalls) {
      if (!existingToolResultIds.has(tc.id)) {
        // 插入合成的空结果
        result.push({
          role: "toolResult",
          toolCallId: tc.id,
          toolName: tc.name,
          content: [{ type: "text", text: "No result provided" }],
          isError: true,
          timestamp: Date.now(),
        } as ToolResultMessage);
      }
    }
    pendingToolCalls = [];
    existingToolResultIds = new Set();
  }
};

for (const msg of transformed) {
  if (msg.role === "assistant") {
    closePendingToolCalls();  // 关闭上一个 assistant 的孤立调用
    
    const toolCalls = assistantMsg.content.filter((b) => b.type === "toolCall");
    if (toolCalls.length > 0) {
      pendingToolCalls = toolCalls;
      existingToolResultIds = new Set();
    }
    
    result.push(msg);
  } else if (msg.role === "toolResult") {
    existingToolResultIds.add(msg.toolCallId);
    result.push(msg);
  } else if (msg.role === "user") {
    closePendingToolCalls();  // 新用户消息中断工具流程
    result.push(msg);
  }
}

closePendingToolCalls();  // 处理末尾的孤立调用
```

**关键策略**：
- 追踪每个 assistant 消息的 tool calls
- 遇到新 assistant/user 消息或会话结束时，检查是否有未匹配的 tool call
- 为每个孤立的 tool call 插入标记为错误的合成结果

**Python 实现**：
```python
def insert_synthetic_results(messages: list[Message]) -> list[Message]:
    result = []
    pending_calls = []
    existing_ids = set()
    
    def close_pending():
        for tc in pending_calls:
            if tc.id not in existing_ids:
                result.append(Message(
                    role="tool",
                    content="No result provided",
                    tool_call_id=tc.id
                ))
        pending_calls.clear()
        existing_ids.clear()
    
    for msg in messages:
        if msg.role == "assistant":
            close_pending()
            if msg.tool_calls:
                pending_calls.extend(msg.tool_calls)
            result.append(msg)
        elif msg.role == "tool":
            existing_ids.add(msg.tool_call_id)
            result.append(msg)
        elif msg.role == "user":
            close_pending()
            result.append(msg)
        else:
            result.append(msg)
    
    close_pending()
    return result
```

#### 修复案例 6：跳过错误/中止的 Assistant 消息

**问题**：错误或中止的 assistant 消息可能包含不完整内容（如只有 reasoning 没有 message、不完整的 tool calls）。

**pi 的修复**（第196-203行）：

```typescript
if (msg.role === "assistant") {
  const assistantMsg = msg as AssistantMessage;
  
  // 跳过错误/中止的消息
  if (assistantMsg.stopReason === "error" || assistantMsg.stopReason === "aborted") {
    continue;  // 不加入结果
  }
  
  // ... 正常处理
  result.push(msg);
}
```

**原因**：
- 不完整的 turn 不应重放
- 可能导致 API 错误（如 OpenAI 的 "reasoning without following item"）
- 模型应从最后有效状态重试

**Python 实现**：
```python
def filter_errored_messages(messages: list[Message]) -> list[Message]:
    return [
        msg for msg in messages
        if not (msg.role == "assistant" and 
                getattr(msg, 'stop_reason', None) in ('error', 'aborted'))
    ]
```

#### 修复案例 7：System 消息的特殊处理

**问题**：system 消息落在 tool call 和其结果之间会导致 API 错误（某些 provider 要求 tool result 紧跟 tool call）。

**pi 的修复**（第164-186行）：

```typescript
const heldSystemMessages: Message[] = [];

const closePendingToolCalls = () => {
  // 先插入合成结果
  for (const tc of pendingToolCalls) {
    if (!existingToolResultIds.has(tc.id)) {
      result.push(/* synthetic result */);
    }
  }
  
  // 再释放 held 的 system 消息
  result.push(...heldSystemMessages);
  heldSystemMessages.length = 0;
};

for (const msg of transformed) {
  if (msg.role === "system") {
    if (pendingToolCalls.length > 0) {
      heldSystemMessages.push(msg);  // 暂存
    } else {
      result.push(msg);  // 直接添加
    }
  }
  // ...
}
```

**策略**：
- 如果有 pending tool calls，暂存 system 消息
- 在插入合成结果后才释放 system 消息
- 确保 tool result 紧跟 tool call

**Python 实现**：
```python
def reorder_system_messages(messages: list[Message]) -> list[Message]:
    result = []
    pending_calls = []
    held_system = []
    
    for msg in messages:
        if msg.role == "system" and pending_calls:
            held_system.append(msg)
        else:
            if msg.role in ("assistant", "user"):
                # 释放held的system消息
                result.extend(held_system)
                held_system.clear()
            result.append(msg)
    
    result.extend(held_system)
    return result
```

### 2. 更多厂商支持（Provider Registry）

**packages/ai/src/providers/** 目录包含 30+ 厂商：

```typescript
// packages/ai/src/providers/google.ts (commit 7d75a0e, 第1-15行)
import { googleGenerativeAIApi } from "../api/google-generative-ai.lazy.ts";
import { envApiKeyAuth } from "../auth/helpers.ts";
import { createProvider, type Provider } from "../models.ts";
import { GOOGLE_MODELS } from "./google.models.ts";

export function googleProvider(): Provider<"google-generative-ai"> {
  return createProvider({
    id: "google",
    name: "Google",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta",
    auth: { apiKey: envApiKeyAuth("Gemini API key", ["GEMINI_API_KEY"]) },
    models: Object.values(GOOGLE_MODELS),
    api: googleGenerativeAIApi(),
  });
}
```

**Ollama 支持**（OpenAI-compatible endpoint）：

pi 通过 **OpenAI adapter + 自定义 baseUrl** 支持 Ollama：

```typescript
// packages/ai/src/providers/openai.ts (简化)
export function openaiProvider(options?: { baseUrl?: string }): Provider<"openai-responses"> {
  return createProvider({
    id: "openai",
    baseUrl: options?.baseUrl ?? "https://api.openai.com/v1",
    api: openAIResponsesApi(),
    models: OPENAI_MODELS,
  });
}
```

**使用方式**：
```typescript
const models = createModels({
  providers: [
    openaiProvider({ baseUrl: "http://localhost:11434/v1" }), // Ollama
  ],
});
```

**Python 实现建议**：
```python
class OpenAICompatibleAdapter(OpenAIAdapter):
    """支持 OpenAI 兼容的端点（如 Ollama）"""
    def __init__(self, base_url: str, api_key: str = "dummy", model: str = "llama2"):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
```

> 参考：`packages/ai/src/providers/google.ts` 和 `packages/ai/src/providers/openai.ts`

### 3. Token 计数与限制机制

pi 通过三层机制管理 token：**模型元数据声明**、**实际 usage 解析**、**本地估算**。

#### 模型元数据：contextWindow / maxTokens / cost

**来源**：自动生成脚本（`packages/ai/scripts/generate-models.ts`，约129KB）+ 外部 catalog

运行 `npm run generate-models` 生成所有模型定义到 `packages/ai/src/providers/data/*.json`：

```typescript
{
  "chat": {
    "gpt-4o-mini": {
      "id": "gpt-4o-mini",
      "api": "openai-responses",
      "contextWindow": 128000,
      "maxTokens": 16384,
      "cost": {
        "input": 0.15,      // USD per million tokens
        "output": 0.60,
        "cacheRead": 0.075,
        "cacheWrite": 0.15,
        "tiers": [...]      // 分级定价
      }
    }
  }
}
```

**数据来源**：OpenRouter API catalog、手工维护、AI Gateway定价。自定义模型通过 Radius gateway 配置文件声明。

#### 实际 Usage Token 获取

**从 provider streaming response 实时解析**

**OpenAI**（`packages/ai/src/api/openai-responses-shared.ts` 第659-686行）：

```typescript
const cachedTokens = inputDetails?.cached_tokens || 0;
const cacheWriteTokens = inputDetails?.cache_write_tokens || 0;

output.usage = {
  input: Math.max(0, (response.usage.input_tokens || 0) - cachedTokens - cacheWriteTokens),
  output: response.usage.output_tokens || 0,
  cacheRead: cachedTokens,
  cacheWrite: cacheWriteTokens,
  reasoning: response.usage.output_tokens_details?.reasoning_tokens || 0,
  totalTokens: response.usage.total_tokens || 0,
  cost: { ... },
};
calculateCost(model, output.usage);
```

**Anthropic**（`packages/ai/src/api/anthropic-messages.ts` 第380-393行）：

```typescript
output.usage.cacheRead = event.message.usage.cache_read_input_tokens || 0;
output.usage.cacheWrite = event.message.usage.cache_creation_input_tokens || 0;
output.usage.cacheWrite1h = event.message.usage.cache_creation?.ephemeral_1h_input_tokens || 0;
// Anthropic 1h cache write 按 2x base input 计费
```

**成本计算**（`packages/ai/src/models.ts` 第1206-1226行）：

```typescript
export function calculateCost(model: AnyModel, usage: Usage): Usage["cost"] {
  let rates = model.cost;
  // 分级定价：根据累计 input token 选择 tier
  for (const tier of model.cost.tiers ?? []) {
    if (inputTokens > tier.inputTokensAbove) rates = tier;
  }
  
  const longWrite = usage.cacheWrite1h ?? 0;
  const shortWrite = usage.cacheWrite - longWrite;
  usage.cost.input = (rates.input / 1000000) * usage.input;
  usage.cost.cacheWrite = (rates.cacheWrite * shortWrite + rates.input * 2 * longWrite) / 1000000;
  // ...
}
```

#### 本地 Token 估算（用于 Compaction 决策）

**关键：使用 chars/4 启发式，无真实 tokenizer**

**估算函数**（`packages/coding-agent/src/core/compaction/compaction.ts` 第298-340行）：

```typescript
export function estimateTokens(message: AgentMessage): number {
  let chars = 0;
  switch (message.role) {
    case "system":
      chars = estimateTextAndImageContentChars(system.content);
      if (system.sections) chars += JSON.stringify(system.sections).length;
      return Math.ceil(chars / 4);  // 关键：字符数除以4
    // ...
  }
}
```

**为什么是 chars/4？** GPT tokenizer 的经验法则：英文平均 ~4 字符/token。这是保守估算（高估），确保不会触发 context overflow。

**⚠️ CJK 文本的低估问题**：chars/4 启发式对中文/日文/韩文等 CJK 文本会**严重低估** token 数。常见 tokenizer（OpenAI、Claude）中，CJK 字符通常是 **~1 token/字符甚至更多**（vs 英文的 4 字符/token）。pi 对所有语言统一使用 chars/4，没有 CJK 特殊处理。对 mini-pi 的影响：在中文为主的会话中，实际 token 消耗可能是估算的 4 倍，导致过晚触发 compaction 或意外的 context overflow。建议方案：(1) 使用 `tiktoken` 库精确计算 OpenAI token（`encoding = tiktoken.encoding_for_model("gpt-4o-mini")`），或 (2) 实现 CJK 感知启发式（检测 CJK Unicode 范围 U+4E00-U+9FFF、U+3040-U+30FF 等，对 CJK 字符用 chars/1 或 chars/1.5，其他用 chars/4）。

**Compaction 触发**（`packages/coding-agent/src/core/settings-schema.ts`）：

```typescript
{
  enabled: Type.Boolean({ default: true }),
  reserveTokens: Type.Number({ default: ... }),      // 预留空间
  keepRecentTokens: Type.Number({ default: ... }),   // 保留最近N token
}
```

当 `estimatedTokens > contextWindow - reserveTokens` 时触发，保留最近的消息，旧消息通过 LLM 总结压缩（详见第4章 compaction 机制）。

**Python 实现**：

```python
@dataclass
class Usage:
    input: int
    output: int
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    total_tokens: int = 0
    cost: dict = field(default_factory=dict)

def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)

def should_compact(messages: list, context_window: int, reserve: int = 10000) -> bool:
    estimated = sum(estimate_tokens(msg.content) for msg in messages)
    return estimated > context_window - reserve
```

> ⚠️ **未深入验证**：OpenRouter catalog API端点、Radius gateway配置schema、Compaction阈值默认值需查看完整源码。

### 4. 中止信号（AbortSignal）全链路传递

**packages/ai/src/types.ts** 中的 signal 参数（第158行）：

```typescript
export interface ProviderStreamOptions {
  signal?: AbortSignal;  // 传递给 provider
  // ...
}
```

**agent-loop 中的传递**（packages/agent/src/agent-loop.ts，简化）：

```typescript
async function streamAssistantResponse(
  context: AgentContext,
  config: AgentLoopConfig,
  signal: AbortSignal | undefined,
): Promise<AssistantMessage> {
  // 1. 传递给 LLM provider
  const response = await streamFunction(config.model, llmContext, {
    signal,  // ← 传给 provider
    ...config,
  });
  
  // 2. provider 内部传递给 HTTP fetch
  // 3. fetch 会响应 abort 信号立即取消请求
}

async function executeToolCalls(
  context: AgentContext,
  toolCalls: ToolCall[],
  signal: AbortSignal | undefined,
): Promise<ToolResult[]> {
  const results = [];
  for (const tc of toolCalls) {
    // 传递给工具执行
    const result = await executeTool(tc, context, signal);  // ← 传给工具
    results.push(result);
  }
  return results;
}
```

**bash 工具中的使用**（packages/coding-agent/src/core/tools/bash.ts）：

```typescript
execute: async (args, context) => {
  const result = await operations.exec(command, cwd, {
    signal: context.signal,  // ← 工具接收并传递给子进程
    timeout,
  });
}
```

**Python 实现建议**：
Python 没有内置的 AbortSignal，但可以用 `asyncio` 的取消机制：

```python
import asyncio

async def agent_loop_with_cancellation(provider, messages, cancel_event: asyncio.Event):
    """带取消支持的 agent 循环"""
    try:
        for step in range(10):
            if cancel_event.is_set():
                raise asyncio.CancelledError("User cancelled")
            
            # 调用 provider（需要支持 cancel_event）
            response = await provider.create_completion_async(
                messages, tools, cancel_event=cancel_event
            )
            # ...
    except asyncio.CancelledError:
        print("Agent loop cancelled")
        raise
```

> 参考：`packages/ai/src/types.ts` 第158行，`packages/agent/src/agent-loop.ts`，`packages/coding-agent/src/core/tools/bash.ts`

## 总结对比表

| 特性 | mini-pi (Python) | pi 真实实现 (TypeScript) |
|---|---|---|
| 统一消息格式 | ✅ 扁平化（`content` + `tool_calls`） | ✅ Anthropic 风格（`content` 数组） |
| OpenAI 支持 | ✅ 完整支持 | ✅ 完整支持 |
| Anthropic 支持 | ✅ 完整支持 | ✅ 完整支持 + 扩展思维模式 |
| 流式输出 | ✅ 支持 | ✅ 支持 + 更细粒度的事件 |
| 消息转换与修复 | ❌ | ✅ 7种修复案例（空内容、图像、thinking、ID、孤立调用、错误消息、system顺序）|
| 更多厂商 | ❌ | ✅ 30+ providers（Google、Azure、Mistral等）+ OpenAI 兼容端点（Ollama） |
| Token 预算管理 | ❌ | ✅ 每条消息带 usage；compaction 机制 ⚠️（具体算法未验证）|
| 中止信号 | ❌ | ✅ 全链路 AbortSignal（agent → provider → fetch + tools）|
| 代码行数 | ~400 行 Python | ~2000 行 TypeScript（ai包核心）+ ~1500 行（agent包）|

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
