# 第 1 章：Agent 主循环

## 核心概念

一个编程智能体（coding agent）的心脏是它的**主循环**。这个循环做三件事情：

1. **调用大语言模型（LLM）**：把当前的消息历史发给模型，获取它的响应
2. **执行工具**：如果模型返回了工具调用（tool calls），执行这些工具并收集结果
3. **追加结果并继续**：把工具的执行结果追加到消息历史，回到第 1 步

循环会一直持续，直到模型不再请求任何工具调用——这表示任务已经完成。

```
用户输入
   ↓
[消息数组]
   ↓
调用 LLM ────→ 有工具调用？ ──否──→ 完成！
   ↑              │
   │            是
   │              ↓
   │         执行工具
   │              ↓
   └──────── 追加结果
```

## 我们的实现（Python + OpenAI）

### 文件结构

```
01-loop/
├── src/
│   ├── main.py        # 主程序（~160 行）
│   └── test_main.py   # 单元测试（用 pytest）
└── README.md          # 本文档
```

### 核心代码解析

#### 1. 工具定义（OpenAI 格式，第 18-35 行）

```python
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "执行 bash 命令。可以用来查看文件、运行程序、操作文件系统等。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 bash 命令"
                }
            },
            "required": ["command"]
        }
    }
}
```

**OpenAI 工具格式**：
- `type: "function"`：固定值，表示这是一个函数工具
- `function.name`：工具的唯一标识符
- `function.description`：告诉模型这个工具是干什么的
- `function.parameters`：JSON Schema 定义参数

> **注**：Claude 的工具格式略有不同（扁平结构，没有嵌套的 `function` 字段）。第 2 章会实现统一的适配层。

#### 2. 工具执行（第 39-59 行）

```python
def execute_bash(command: str) -> tuple[str, bool]:
    """执行 bash 工具，返回 (output, is_error)"""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        
        # 截断过长的输出
        if len(output) > 10000:
            output = output[:10000] + "\n...(输出被截断)"
        
        return output, result.returncode != 0
    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时（30秒）", True
    except Exception as e:
        return f"错误: {str(e)}", True
```

关键保护措施：
- **超时**：30 秒后强制终止
- **输出截断**：限制 10000 字符，保护上下文窗口
- **错误处理**：捕获所有异常

#### 3. Agent 主循环（第 63-117 行）

这是整个程序的核心：

```python
def agent_loop(client: OpenAI, messages: List[Dict[str, Any]], max_steps: int = 10) -> None:
    
    for step in range(max_steps):
        # 1. 调用 OpenAI API
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=[BASH_TOOL]
        )
        
        message = response.choices[0].message
        
        # 2. 将 assistant 消息追加到历史
        assistant_message = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": message.tool_calls  # OpenAI 的工具调用列表
        }
        messages.append(assistant_message)
        
        # 3. 检查是否有工具调用
        if not message.tool_calls:
            # 没有工具调用，循环结束
            break
        
        # 4. 执行所有工具调用
        for tool_call in message.tool_calls:
            # 解析参数
            args = json.loads(tool_call.function.arguments)
            output, is_error = execute_bash(args["command"])
            
            # 5. 将工具结果追加到消息历史
            # OpenAI 格式：role="tool"，必须包含 tool_call_id
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            }
            messages.append(tool_message)
        
        # 6. 循环继续
```

**OpenAI 的消息格式**：

- **assistant 消息**：
  - `role: "assistant"`
  - `content`: 模型说的话（可以为 None）
  - `tool_calls`: 工具调用列表，每个包含：
    - `id`: 工具调用的唯一 ID
    - `type`: "function"
    - `function.name`: 工具名
    - `function.arguments`: JSON 字符串格式的参数

- **tool 消息**（工具结果）：
  - `role: "tool"`
  - `tool_call_id`: 对应的工具调用 ID
  - `content`: 工具的输出（字符串）

> **Claude 格式对比**：Claude 不使用独立的 `tool` 消息角色。相反，它在 `assistant` 和 `user` 消息的 `content` 数组中使用 `tool_use` 和 `tool_result` 内容块。这是第 2 章要解决的格式差异。

**停止条件**：
- `message.tool_calls` 为 None 或空列表 → 模型认为任务完成
- 达到最大步数限制（`max_steps`）

#### 4. 环境变量支持（第 128-133 行）

```python
client = OpenAI(
    api_key=api_key,
    base_url=os.environ.get("OPENAI_BASE_URL")  # 支持自定义端点
)
```

支持的环境变量：
- `OPENAI_API_KEY`（必需）：OpenAI API 密钥
- `OPENAI_BASE_URL`（可选）：自定义 API 端点，如 Azure OpenAI
- `OPENAI_MODEL`（可选）：模型名称，默认 `gpt-4o-mini`

## 对比 pi 的真实实现（TypeScript）

我们的 160 行 Python 实现已经能跑起来，但 pi 的生产级循环有 949 行 TypeScript（`packages/agent/src/agent-loop.ts`）。让我们看看差在哪里。

> **注**：以下代码片段来自 pi 的 TypeScript 源码。我们会用中文解释 TS 的语法，即使你不懂 TS 也能理解。

### 1. 双层循环：内层 + 外层

**我们的代码**：只有一个简单的 `for` 循环

**pi 的代码**（agent-loop.ts 第 184-319 行）：

```typescript
// 外层循环：处理 follow-up 消息
while (!aborted) {
  // 内层循环：执行工具调用
  while (hasMoreToolCalls || pendingMessages.length > 0 || explicitContinuation) {
    // ... 调用模型，执行工具
  }
  
  // 检查 follow-up 队列
  const followUpMessages = (await config.getFollowUpMessages?.()) || [];
  if (followUpMessages.length > 0) {
    pendingMessages = followUpMessages;
    continue;
  }
  
  break;
}
```

**TypeScript 语法说明**：
- `while (!aborted)`：当没有中止时循环
- `const`：定义常量
- `await`：等待异步操作
- `?.`：可选链操作符，如果方法存在就调用
- `|| []`：如果左边为空，返回空数组

**为什么需要两层循环？**

- **内层循环**：处理"还有工具要执行"的情况
- **外层循环**：处理"本来要停了，但用户又输入了新消息"的情况（follow-up）

这让 pi 支持**边运行边输入**：agent 正在执行时，你可以打字干预它。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L184-L319

### 2. 并行 vs 串行工具执行

**我们的代码**：用一个 `for` 循环，串行执行所有工具

**pi 的代码**（agent-loop.ts 第 516-523 行）：

```typescript
const hasSequentialToolCall = toolCalls.some(
  (tc) => currentContext.tools?.find((t) => t.name === tc.name)?.executionMode === "sequential",
);

if (config.toolExecution === "sequential" || hasSequentialToolCall) {
  return executeToolCallsSequential(...);
}
return executeToolCallsParallel(...);
```

**TypeScript 语法说明**：
- `.some()`：检查数组是否有元素满足条件（类似 Python 的 `any()`）
- `(tc) => ...`：箭头函数（类似 Python 的 `lambda`）
- `.find()`：找到第一个满足条件的元素

**并行模式的好处**：
- 模型同时请求 5 个文件？可以并发读取
- 但有些工具必须串行（比如修改同一个文件）

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L516-L523

### 3. 处理被截断的工具调用

**场景**：模型的输出超过了 token 限制，被强制截断了。此时工具调用的 JSON 参数可能是不完整的。

**pi 的处理**（agent-loop.ts 第 265-270 行）：

```typescript
const executedToolBatch =
  message.stopReason === "length"
    ? await failToolCallsFromTruncatedMessage(toolCalls, emit)
    : await executeToolCalls(currentContext, message, config, signal, emit);
```

**TypeScript 语法**：
- `? ... : ...`：三元运算符（类似 Python 的 `... if ... else ...`）

**当 `stopReason === "length"` 时**：
- 不执行任何工具
- 直接返回错误结果："输出被截断，工具调用未执行"
- 让模型在下一轮看到这个错误，自行调整

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L265-L270

### 4. 流式输出

**我们的代码**：等整个响应生成完才返回

**pi 的实现**（agent-loop.ts 第 381-459 行）：

```typescript
async function streamAssistantResponse(...): Promise<AssistantMessage> {
  const response = await streamFunction(config.model, llmContext, {
    ...config,
    signal,
  });
  
  for await (const event of response) {
    switch (event.type) {
      case "start":
        partialMessage = event.partial;
        context.messages.push(partialMessage);
        await emit({ type: "message_start", message: { ...partialMessage } });
        break;
      
      case "text_delta":
      case "toolcall_delta":
        partialMessage = event.partial;
        context.messages[context.messages.length - 1] = partialMessage;
        await emit({ type: "message_update", ... });
        break;
      
      case "done":
        await emit({ type: "message_end", message: finalMessage });
        return finalMessage;
    }
  }
}
```

**TypeScript 语法**：
- `for await (const event of response)`：异步迭代（类似 Python 的 `async for`）
- `switch (event.type)`：多分支判断（类似 Python 的 `match-case`）
- `{ ...partialMessage }`：对象展开运算符（类似 Python 的 `{**dict}`）

**好处**：
- **边生成边显示**：用户不用干等
- **提前中止**：看到模型走错了，可以立即停止

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L381-L459

### 5. 工具调用的钩子（Before / After）

**pi 的实现**（types.ts）：

```typescript
export interface ToolCallHooks {
  beforeToolCall?: (context, toolCall, assistantMessage) => Promise<{
    action: 'block' | 'continue';
    result?: AgentToolResult;  // 如果 block，返回这个假结果
  }>;
  
  afterToolCall?: (context, toolCall, result) => Promise<{
    result: AgentToolResult;  // 可以改写真实结果
  }>;
}
```

**TypeScript 语法**：
- `interface`：接口定义（类似 Python 的 `Protocol` 或 `TypedDict`）
- `export`：导出类型
- `?:`：可选属性
- `'block' | 'continue'`：字面量联合类型（只能是这两个值之一）

**用途**：
- **拦截危险命令**：在 `beforeToolCall` 里检测到 `rm -rf`，弹出确认框
- **改写结果**：在 `afterToolCall` 里给结果加摘要
- **记录审计日志**：每次工具调用都记下来

## 总结对比表

| 特性 | mini-pi (Python + OpenAI) | pi 真实实现 (TypeScript) |
|---|---|---|
| 基础循环 | ✅ 单层 for 循环 | ✅ 双层 while 循环 |
| 工具执行 | ✅ 串行执行 bash | ✅ 并行 + 串行可选 |
| 停止条件 | ✅ 无工具调用或最大步数 | ✅ 更多条件（error、aborted、follow-up） |
| 流式输出 | ❌ 等待完整响应 | ✅ 边生成边显示 |
| 工具调用钩子 | ❌ | ✅ Before / After |
| 截断处理 | ❌ | ✅ 检测并跳过残缺调用 |
| 消息格式 | OpenAI（tool_calls + role="tool"） | 统一格式（兼容多厂商） |
| 代码行数 | ~160 行 Python | ~949 行 TypeScript |

## 如何运行

### 前置条件

```bash
# 安装依赖
pip install -r requirements.txt

# 设置 API key
export OPENAI_API_KEY="your-api-key-here"

# 可选：使用自定义模型
export OPENAI_MODEL="gpt-4o"

# 可选：使用自定义端点（如 Azure OpenAI）
export OPENAI_BASE_URL="https://your-endpoint.openai.azure.com/v1"
```

### 运行 Agent

```bash
python3 01-loop/src/main.py
```

你也可以修改 `main.py` 第 147 行的用户请求，自定义任务：

```python
{
    "role": "user",
    "content": "你的自定义任务"
}
```

### 运行测试（无需 API key）

```bash
# 方法 1：使用 pytest
pytest 01-loop/src/test_main.py -v

# 方法 2：直接运行测试文件
python3 01-loop/src/test_main.py
```

## 练习

### 练习 1：添加重试机制

**目标**：当工具执行失败时（返回码非 0），自动重试最多 3 次。

**提示**：
- 修改 `execute_bash` 函数
- 用 `for i in range(3)` 尝试最多 3 次
- 每次失败后等待 1 秒：`time.sleep(1)`

### 练习 2：添加 token 计数

**目标**：统计每轮循环消耗了多少 token，并在结束时打印总数和预估花费。

**提示**：
- OpenAI 的响应对象有 `usage` 属性：
  ```python
  response.usage.prompt_tokens
  response.usage.completion_tokens
  response.usage.total_tokens
  ```
- 维护一个累加器：`total_tokens = {"prompt": 0, "completion": 0}`
- 循环结束后打印并计算花费（参考 OpenAI 定价页面）

**参考定价**（gpt-4o-mini，2026-10）：
- 输入：$0.150 / 1M tokens
- 输出：$0.600 / 1M tokens

## OpenAI vs Claude 格式对比

本章使用 OpenAI 格式。第 2 章会添加 Claude 支持。两者的主要差异：

### 工具定义

**OpenAI**：
```python
{
    "type": "function",
    "function": {
        "name": "bash",
        "parameters": {...}
    }
}
```

**Claude**：
```python
{
    "name": "bash",
    "input_schema": {...}
}
```

### 消息格式

**OpenAI**：
```python
# assistant 消息
{"role": "assistant", "content": "...", "tool_calls": [...]}

# 工具结果
{"role": "tool", "tool_call_id": "...", "content": "..."}
```

**Claude**：
```python
# assistant 消息（content 是数组）
{"role": "assistant", "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "...", "name": "bash", "input": {...}}
]}

# 工具结果（在 user 消息中）
{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "...", "content": "..."}
]}
```

**第 2 章的任务**：实现一个统一的适配层，让同一份代码可以无缝切换 OpenAI 和 Claude。

## pi 的 Steering vs Follow-up 消息机制

我们的简单循环只有一个停止条件：模型不再请求工具调用。但 pi 有一个更强大的功能：**在 agent 运行时实时插入新消息**。

pi 提供两种队列模式：**steering**（转向）和 **follow-up**（后续）。它们的核心区别在于**何时注入**。

### 注入时机的区别

**Steering 消息**：在**内层循环的每轮开始前**注入

**pi 的代码**（agent-loop.ts 第 175-206 行）：

```typescript
// Check for steering messages at start (user may have typed while waiting)
let pendingMessages: AgentMessage[] = (await config.getSteeringMessages?.()) || [];

// Outer loop: continues when queued follow-up messages arrive after agent would stop
while (true) {
  let hasMoreToolCalls = true;

  // Inner loop: process tool calls and steering messages
  while (hasMoreToolCalls || pendingMessages.length > 0) {
    let preparedMessages: AgentMessage[] = [];
    if (lastCompletedTurn) {
      // ...准备下一轮...
      
      // Preparation can be long-running (for example, compaction). Pick up steering
      // queued while it ran. Only poll again if the earlier poll returned nothing;
      // otherwise one-at-a-time mode would deliver two messages in this turn.
      if (pendingMessages.length === 0) {
        pendingMessages = (await config.getSteeringMessages?.()) || [];
      }
      await emit({ type: "turn_start" });
    }
    
    // Process prepared and queued messages before the next assistant response.
    for (const message of declareToolChanges(..., pendingMessages)) {
      // 注入 steering 消息
    }
    pendingMessages = [];
    
    // 调用模型...
    // 执行工具...
    
    // 每轮结束前再次检查 steering
    pendingMessages = (await config.getSteeringMessages?.()) || [];
    if (hasMoreToolCalls || pendingMessages.length > 0) {
      explicitContinuation = false;
    }
  }
  
  // (外层循环继续...)
}
```

**TypeScript 语法说明**：
- `while (true)`：无限循环，通过内部 `break` 退出
- `hasMoreToolCalls || pendingMessages.length > 0`：只要还有工具调用**或**有 steering 消息，内层循环就继续
- `await emit({ type: "turn_start" })`：发出事件通知（类似 Python 的事件系统）
- `for (const message of array)`：遍历数组（类似 Python 的 `for message in array`）

**关键行为**：
- Steering 消息在**每个 turn 开始前**都会被检查和注入
- **即使还有待执行的工具调用**，新的 steering 消息也会被插入
- 内层循环条件：`hasMoreToolCalls || pendingMessages.length > 0`

**Follow-up 消息**：只在**外层循环**中检查，即 agent **本该停止时**才注入

**pi 的代码**（agent-loop.ts 第 300-319 行）：

```typescript
  } // 内层循环结束

  // Agent would stop here. Check for follow-up messages.
  const followUpMessages = (await config.getFollowUpMessages?.()) || [];
  if (followUpMessages.length > 0) {
    // Set as pending so inner loop processes them
    explicitContinuation = false;
    pendingMessages = followUpMessages;
    continue;  // 继续外层循环
  }

  // No natural request was selected, so fulfill the continuation decision with one context-only turn.
  if (explicitContinuation) {
    explicitContinuation = false;
    continue;
  }

  // No more messages, exit
  break;
}
```

**关键行为**：
- Follow-up 消息**只在内层循环退出后**才检查
- 此时意味着：没有更多工具调用 **且** 没有 steering 消息
- 如果有 follow-up 消息，将它们转为 `pendingMessages` 并重新进入内层循环

> 参考：  
> - 内层循环：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/agent/src/agent-loop.ts#L182-L299  
> - 外层循环：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/agent/src/agent-loop.ts#L302-L319

### 用户如何选择：TUI 键绑定

pi 不会自动判断消息应该是 steering 还是 follow-up，而是让**用户通过键盘选择**。

**TUI 键绑定**（keybindings.ts 第 67-70 行）：

```typescript
"app.message.followUp": {
  defaultKeys: windowsKeybindings ? "ctrl+q" : "alt+enter",
  description: "Queue follow-up message",
},
```

**键盘快捷键**：
- **Enter**（`tui.input.submit`）：
  - **不在 streaming 时**：正常提交，开始新的 agent 运行
  - **在 streaming 时**：作为 **steering** 消息队列（通过 `streamingBehavior: "steer"`）
  
- **Alt+Enter / Ctrl+Q**（`app.message.followUp`）：
  - Windows/WSL：`Ctrl+Q`
  - macOS/Linux：`Alt+Enter`
  - 总是队列为 **follow-up** 消息

**实现逻辑**（interactive-mode.ts 第 3200、4503 行）：

```typescript
// 普通 Enter 的处理
this.defaultEditor.onSubmit = async (text: string) => {
  // ... 处理各种命令 ...
  
  // 如果在 streaming，调用 prompt 时指定 steer
  if (this.session.isStreaming) {
    await this.session.prompt(text, { streamingBehavior: "steer" });
  } else {
    // 不在 streaming 时，正常启动新的运行
    await this.session.prompt(text);
  }
};

// Alt+Enter 的处理
private async handleFollowUp(text: string): Promise<void> {
  // Alt+Enter queues a follow-up message (waits until agent finishes)
  if (this.session.isStreaming) {
    this.editor.addToHistory?.(text);
    this.editor.setText("");
    await this.session.prompt(text, { streamingBehavior: "followUp" });
    this.updatePendingMessagesDisplay();
  }
  // If not streaming, Alt+Enter acts like regular Enter (trigger onSubmit)
  else if (this.editor.onSubmit) {
    this.editor.setText("");
    this.editor.onSubmit(text);
  }
}
```

**TypeScript 语法说明**：
- `async (text: string) => { ... }`：异步箭头函数（类似 Python 的 `async def`）
- `this.session.isStreaming`：检查 session 是否正在运行
- `{ streamingBehavior: "steer" }`：对象字面量，指定选项（类似 Python 的字典）
- `?.`：可选链，如果方法存在就调用

> 参考：  
> - 键绑定定义：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/coding-agent/src/core/keybindings.ts#L67-L70  
> - Enter 处理：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L3200  
> - Alt+Enter 处理：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L4503-L4517

### Agent 类方法和队列模式

**Agent 类的 API**（agent.ts 第 280-315 行）：

```typescript
/** Controls how queued steering messages are drained. */
set steeringMode(mode: QueueMode) {
  this.steeringQueue.mode = mode;
}

get steeringMode(): QueueMode {
  return this.steeringQueue.mode;
}

/** Controls how queued follow-up messages are drained. */
set followUpMode(mode: QueueMode) {
  this.followUpQueue.mode = mode;
}

get followUpMode(): QueueMode {
  return this.followUpQueue.mode;
}

/** Queue a message to be injected after the current assistant turn finishes. */
steer(message: AgentMessage): void {
  this.steeringQueue.enqueue(message);
}

/** Queue a message to be delivered when the agent would otherwise stop. */
followUp(message: AgentMessage): void {
  this.followUpQueue.enqueue(message);
}

/** Remove all queued steering messages. */
clearSteeringQueue(): void {
  this.steeringQueue.clear();
}

/** Remove all queued follow-up messages. */
clearFollowUpQueue(): void {
  this.followUpQueue.clear();
}
```

**TypeScript 语法说明**：
- `set propertyName(value: Type)`：setter 方法（类似 Python 的 `@property` setter）
- `get propertyName(): Type`：getter 方法（类似 Python 的 `@property`）
- `: void`：返回类型为 void（无返回值，类似 Python 的 `-> None`）

**队列模式**（types.ts 第 49-55 行）：

```typescript
/**
 * Controls how many queued user messages are injected when the agent loop reaches a queue drain point.
 *
 * - "all": drain and inject every queued message at that point.
 * - "one-at-a-time": drain and inject only the oldest queued message, leaving the rest queued for later drain points.
 */
export type QueueMode = "all" | "one-at-a-time";
```

**默认值**（agent.ts 第 247-248 行）：

```typescript
this.steeringQueue = new PendingMessageQueue(runtimeOptions.steeringMode ?? "one-at-a-time");
this.followUpQueue = new PendingMessageQueue(runtimeOptions.followUpMode ?? "one-at-a-time");
```

**TypeScript 语法说明**：
- `runtimeOptions.steeringMode ?? "one-at-a-time"`：空值合并运算符，如果左边是 null/undefined 则返回右边（类似 Python 的 `or` 但更严格）

**队列模式说明**：
- **`"one-at-a-time"`**（默认）：
  - 每次 drain 点只注入队列中最老的一条消息
  - 其余消息留在队列，等待下一个 drain 点
  - 让 agent 逐条处理用户的多个输入
  
- **`"all"`**：
  - 一次性注入队列中的所有消息
  - 适合批量处理多条指令

> 参考：  
> - Agent 方法：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/agent/src/agent.ts#L280-L315  
> - QueueMode 定义：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/agent/src/types.ts#L49-L55  
> - 默认值：https://github.com/earendil-works/pi/blob/19cb534c3b6e3871458a8fa59e98f0312c50e995/packages/agent/src/agent.ts#L247-L248

### 实际使用场景

**Steering（转向）**：
- **场景**：agent 正在执行一个长时间的任务，你发现它走错了方向
- **行为**：在下一个 turn 开始前立即注入你的纠正消息
- **示例**：
  ```
  Agent: 正在执行 "find / -name '*.log'"...
  你按 Enter 输入: "停！只搜索 /var/log 目录"
  → 立即在下一轮注入，引导 agent 改变策略
  ```

**Follow-up（后续）**：
- **场景**：agent 完成了当前任务，你想让它继续做下一件事
- **行为**：等到 agent 完全停止后才注入
- **示例**：
  ```
  Agent: "已完成文件列表，共 42 个文件"
  你按 Alt+Enter 输入: "现在统计每个文件的行数"
  → 等待 agent 停止后再注入，避免打断正常流程
  ```

### 总结表

| 特性 | Steering（转向） | Follow-up（后续） |
|---|---|---|
| **注入时机** | 内层循环每轮开始前 | 外层循环，agent 本该停止时 |
| **检查位置** | 第 175、205、295 行 | 第 302 行 |
| **典型用途** | 实时纠正、紧急干预 | 任务完成后的后续指令 |
| **键盘快捷键** | Enter（streaming 时） | Alt+Enter / Ctrl+Q |
| **API 方法** | `agent.steer(message)` | `agent.followUp(message)` |
| **队列模式** | `agent.steeringMode` | `agent.followUpMode` |
| **默认模式** | `"one-at-a-time"` | `"one-at-a-time"` |
| **循环条件** | `hasMoreToolCalls \|\| pendingMessages.length > 0` | 内层循环退出后检查 |

## 下一步

完成了基础循环，你已经理解了 agent 的核心原理。但是：

- **只支持 OpenAI**：换成 Claude 需要重写消息格式
- **没有文件工具**：只能靠 bash 读写文件，不够精细
- **没有会话管理**：重启就丢失所有历史

**第 2 章**会解决第一个问题：构建一个统一的多模型适配层（OpenAI + Claude，对比 pi 的 TypeScript 实现）。
