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

## 我们的实现（Python）

### 文件结构

```
01-loop/
├── src/
│   ├── main.py        # 主程序（~200 行）
│   └── test_main.py   # 单元测试（用 pytest）
└── README.md          # 本文档
```

### 核心代码解析

#### 1. 工具定义（第 19-33 行）

```python
BASH_TOOL = {
    "name": "bash",
    "description": "执行 bash 命令。可以用来查看文件、运行程序、操作文件系统等。",
    "input_schema": {
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
```

工具定义是一个字典，包含三部分：
- **name**：工具的唯一标识符
- **description**：告诉模型这个工具是干什么的
- **input_schema**：参数的 JSON Schema 定义（Anthropic API 格式）

#### 2. 工具执行（第 37-67 行）

```python
def execute_bash(command: str) -> tuple[str, bool]:
    """执行 bash 工具"""
    try:
        # 执行命令，30秒超时
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # 合并 stdout 和 stderr
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
- **超时**：30 秒后强制终止，防止命令无限期运行
- **输出截断**：限制 10000 字符，保护上下文窗口
- **错误处理**：捕获所有异常并返回错误信息

#### 3. Agent 主循环（第 91-172 行）

这是整个程序的核心：

```python
def agent_loop(client: Anthropic, messages: List[Dict[str, Any]], max_steps: int = 10) -> None:
    
    for step in range(max_steps):
        # 1. 调用 LLM
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            system="你是一个有帮助的编程助手。使用 bash 工具来完成用户的请求。",
            tools=[BASH_TOOL],
            messages=messages
        )
        
        # 2. 将 assistant 消息追加到历史
        assistant_message = {
            "role": "assistant",
            "content": response.content  # content 是一个 ContentBlock 列表
        }
        messages.append(assistant_message)
        
        # 3. 检查是否有工具调用
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        
        if not tool_uses:
            # 没有工具调用，循环结束
            break
        
        # 4. 执行所有工具调用
        tool_results = []
        for tool_use in tool_uses:
            result = execute_tool_call(tool_use)
            tool_results.append(result)
        
        # 5. 将工具结果追加到消息历史
        user_message = {
            "role": "user",
            "content": tool_results  # Anthropic API 要求工具结果在 user 消息中
        }
        messages.append(user_message)
        
        # 6. 循环继续
```

**循环的每一步**：

1. **调用模型**：传入完整的消息历史 + 工具定义
2. **保存响应**：把 `assistant` 消息追加到历史
3. **检查工具调用**：从响应的 `content` 里筛选出 `tool_use` 类型的块
4. **没有工具调用 → 结束**：模型认为任务完成了
5. **有工具调用 → 执行**：逐个执行并收集结果
6. **追加工具结果**：打包成 `user` 消息
7. **循环继续**：带着更新后的历史，回到第 1 步

**停止条件**：
- 模型不再请求工具调用
- 达到最大步数限制（`max_steps`），防止无限循环

## 对比 pi 的真实实现（TypeScript）

我们的 150 行 Python 实现已经能跑起来，但 pi 的生产级循环有 949 行 TypeScript（`packages/agent/src/agent-loop.ts`）。让我们看看差在哪里。

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
- `while (!aborted)`：当没有中止时循环（`!` 是"非"运算符）
- `const` 定义常量（类似 Python 的变量，但不能重新赋值）
- `await`：等待异步操作完成（类似 Python 的 `await`）
- `?.`：可选链操作符。`config.getFollowUpMessages?.()` 意思是"如果 getFollowUpMessages 存在就调用它"
- `|| []`：如果左边是 null/undefined，返回右边的空数组

**为什么需要两层循环？**

- **内层循环**：处理"还有工具要执行"的情况（和我们的类似）
- **外层循环**：处理"本来要停了，但用户又输入了新消息"的情况（follow-up）

这让 pi 支持**边运行边输入**：agent 正在执行时，你可以打字干预它。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L184-L319

### 2. Steering 消息：实时插入新指令

**pi 的实现**（agent-loop.ts 第 201-207 行）：

```typescript
// 准备下一轮前，先拉取 steering 消息
pendingMessages = (await config.getSteeringMessages?.()) || [];

// Preparation can be long-running (for example, compaction). 
// Pick up steering queued while it ran.
if (pendingMessages.length > 0) {
  pendingMessages = (await config.getSteeringMessages?.()) || [];
}
```

**什么是 steering？**

在 agent 循环的**任意时刻**，用户可以往队列里推消息。下一轮开始前，这些消息会被插入上下文。

典型场景：
- Agent 正在执行一个长时间的命令
- 你发现它走错了方向
- 你打字说"停！别那么做，用另一种方法"
- 这条消息会在下一轮被插入，引导 agent 改变策略

我们的简单循环做不到这一点——必须等当前步骤完成。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L201-L207

### 3. 工具调用的钩子（Before / After）

**pi 的实现**（agent-loop.ts 第 508-523 行）：

```typescript
async function executeToolCalls(
  currentContext: AgentContext,
  assistantMessage: AssistantMessage,
  config: AgentLoopConfig,
  signal: AbortSignal | undefined,
  emit: AgentEventSink,
): Promise<ExecutedToolCallBatch> {
  // 每个工具调用都会经过：
  // 1. prepareToolCall()  -> 可以在这里拦截
  // 2. executePreparedToolCall()  -> 真正执行
  // 3. finalizeExecutedToolCall()  -> 可以改写结果
}
```

**TypeScript 语法说明**：
- `async function`：异步函数（类似 Python 的 `async def`）
- `Promise<ExecutedToolCallBatch>`：返回类型标注，表示返回一个 Promise（类似 Python 的 `-> Awaitable[ExecutedToolCallBatch]`）
- `: AbortSignal | undefined`：类型标注，表示参数可以是 AbortSignal 或 undefined（类似 Python 的 `signal: AbortSignal | None`）

在 `types.ts` 中定义的 `ToolCallHooks`：

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

**TypeScript 语法说明**：
- `interface`：接口定义（类似 Python 的 `TypedDict` 或 `Protocol`）
- `export`：导出这个类型，让其他文件可以使用
- `?:`：可选属性，表示这个字段可以不存在
- `'block' | 'continue'`：字面量联合类型，表示只能是这两个字符串之一

**用途**：

- **拦截危险命令**：在 `beforeToolCall` 里检测到 `rm -rf`，弹出确认框
- **改写结果**：在 `afterToolCall` 里给结果加个摘要，或者过滤敏感信息
- **记录审计日志**：每次工具调用都记下来

### 4. 并行 vs 串行工具执行

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
- `.some()`：数组方法，检查是否有元素满足条件（类似 Python 的 `any()`）
- `(tc) => ...`：箭头函数（类似 Python 的 `lambda tc: ...`）
- `.find()`：数组方法，找到第一个满足条件的元素（类似 Python 的 `next(filter(...))` 或列表推导式）

**并行模式的好处**：

- 模型同时请求 5 个文件？可以并发读取
- 但有些工具必须串行（比如修改同一个文件）

pi 允许**每个工具单独声明执行模式**。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L516-L523

### 5. 处理被截断的工具调用

**场景**：模型的输出超过了 token 限制，被强制截断了。此时工具调用的 JSON 参数可能是不完整的。

**pi 的处理**（agent-loop.ts 第 265-270 行）：

```typescript
const executedToolBatch =
  message.stopReason === "length"
    ? await failToolCallsFromTruncatedMessage(toolCalls, emit)
    : await executeToolCalls(currentContext, message, config, signal, emit);
```

**TypeScript 语法说明**：
- `? ... : ...`：三元运算符（类似 Python 的 `... if ... else ...`）
- 整体意思：如果 stopReason 是 "length"，就调用 failToolCallsFromTruncatedMessage，否则调用 executeToolCalls

**当 `stopReason === "length"` 时**：
- 不执行任何工具
- 直接返回错误结果："输出被截断，工具调用未执行"
- 让模型在下一轮看到这个错误，自行调整

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L265-L270

### 6. 流式输出

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

**TypeScript 语法说明**：
- `for await (const event of response)`：异步迭代（类似 Python 的 `async for event in response`）
- `switch (event.type)`：多分支条件判断（类似 Python 的 `match-case` 或多个 `if-elif`）
- `{ ...partialMessage }`：对象展开运算符，复制对象的所有属性（类似 Python 的 `{**partial_message}`）

**好处**：
- **边生成边显示**：用户不用干等，可以实时看到模型在说什么
- **提前中止**：看到模型走错了，可以按 Ctrl+C 立即停止

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L381-L459

### 7. 工具声明的动态管理

**pi 的代码**（agent-loop.ts 第 324-370 行）：

```typescript
function declareToolChanges(context: AgentContext, pendingMessages: AgentMessage[]): AgentMessage[] {
  const changes = getToolStateChanges(
    getCurrentTools([...context.messages, ...baseline]),
    (context.tools ?? []).map(toToolDeclaration),
  );
  
  if (changes.toolsAdded.length > 0 || changes.toolsRemoved.length > 0) {
    // 插入一条 system 消息，告诉模型哪些工具新增了、哪些移除了
  }
}
```

**TypeScript 语法说明**：
- `[...array]`：数组展开运算符，创建数组的副本（类似 Python 的 `[*array]` 或 `list(array)`）
- `??`：空值合并运算符，如果左边是 null/undefined 则返回右边（类似 Python 的 `or`）
- `.map(func)`：数组方法，对每个元素应用函数（类似 Python 的 `map()` 或列表推导式）

**为什么需要动态管理？**

pi 支持**扩展在运行时注册新工具**。比如：
- 用户加载了一个插件，插件注册了一个自定义工具 `search_database`
- 下一轮循环前，系统会插入一条消息："新增工具：search_database"
- 模型看到这条消息，知道它现在可以用这个新工具了

我们的实现里，工具列表是写死的。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L324-L370

### 8. 中止信号（AbortSignal）

**pi 的所有异步操作都接受一个 `signal` 参数**：

```typescript
async function agentLoop(..., signal: AbortSignal | undefined) {
  // 传递给模型调用
  const response = await streamFunction(model, context, { signal });
  
  // 传递给工具执行
  await executeToolCalls(..., signal);
  
  // 传递给钩子
  await config.beforeToolCall?.(..., signal);
}
```

**用途**：
- 用户按了 Ctrl+C → 触发 `signal.abort()`
- 所有正在进行的操作会优雅地停止
- 不会留下半成品或僵尸进程

我们的 Python 版本可以用 `KeyboardInterrupt` 异常来实现类似功能。

## 总结对比表

| 特性 | mini-pi (Python) | pi 真实实现 (TypeScript) |
|---|---|---|
| 基础循环 | ✅ 单层 for 循环 | ✅ 双层 while 循环 |
| 工具执行 | ✅ 串行执行 bash | ✅ 并行 + 串行可选 |
| 停止条件 | ✅ 无工具调用或达到最大步数 | ✅ 更多条件（error、aborted、follow-up） |
| 流式输出 | ❌ 等待完整响应 | ✅ 边生成边显示 |
| 实时插入指令 | ❌ | ✅ Steering 消息 |
| 工具调用钩子 | ❌ | ✅ Before / After |
| 截断处理 | ❌ | ✅ 检测并跳过残缺调用 |
| 中止信号 | ❌ | ✅ AbortSignal 全链路 |
| 动态工具注册 | ❌ | ✅ 运行时声明变更 |
| 代码行数 | ~200 行 Python | ~949 行 TypeScript |

## 如何运行

### 前置条件

```bash
# 在项目根目录安装依赖
pip install -r requirements.txt

# 设置 API key
export ANTHROPIC_API_KEY="your-api-key-here"
```

### 运行 Agent

```bash
# 直接运行
python3 01-loop/src/main.py
```

你也可以修改 `main.py` 第 193 行的用户请求，自定义任务：

```python
messages = [
    {
        "role": "user",
        "content": "你的自定义任务"
    }
]
```

### 运行测试（无需 API key）

```bash
# 方法 1：使用 pytest
pytest 01-loop/src/test_main.py -v

# 方法 2：直接运行测试文件
python3 01-loop/src/test_main.py
```

测试用模拟的 LLM 响应来验证循环逻辑，不需要真实的 API 调用。

## 练习

### 练习 1：添加重试机制

**目标**：当工具执行失败时，自动重试最多 3 次。

**提示**：
- 修改 `execute_bash` 函数，添加一个 `retries` 参数
- 用 `for` 循环尝试最多 3 次
- 每次失败后等待 1 秒再重试（`time.sleep(1)`）

**验证**：
- 运行 `execute_bash("exit 1")` 应该重试 3 次
- 运行 `execute_bash("ls")` 应该立即成功，不重试

### 练习 2：添加 token 计数

**目标**：统计每轮循环消耗了多少 token，并在结束时打印总数。

**提示**：
- Anthropic 的响应对象有 `usage` 属性：`response.usage.input_tokens` 和 `response.usage.output_tokens`
- 在 `agent_loop` 函数里维护一个字典：`total_tokens = {"input": 0, "output": 0}`
- 每轮后累加：`total_tokens["input"] += response.usage.input_tokens`
- 循环结束后打印

**扩展**：
- 根据 Anthropic 的定价（参考 https://www.anthropic.com/pricing），计算总花费
- Claude 3.5 Sonnet 的价格：输入 $3/M tokens，输出 $15/M tokens

**参考实现**：
```python
# 在循环前
total_tokens = {"input": 0, "output": 0}

# 在循环中每次响应后
total_tokens["input"] += response.usage.input_tokens
total_tokens["output"] += response.usage.output_tokens

# 循环结束后
total = total_tokens["input"] + total_tokens["output"]
cost = (total_tokens["input"] * 3 + total_tokens["output"] * 15) / 1_000_000
print(f"\n💰 Token 使用: {total_tokens['input']} 输入 + {total_tokens['output']} 输出 = {total} 总计")
print(f"💵 预估花费: ${cost:.6f}")
```

## 下一步

完成了基础循环，你已经理解了 agent 的核心原理。但是：

- **只支持一家厂商**：换成 OpenAI 或 Gemini 就得重写
- **没有文件工具**：只能靠 bash 读写文件，不够精细
- **没有会话管理**：重启就丢失所有历史

**第 2 章**会解决第一个问题：构建一个统一的多模型适配层（Python 实现，对比 pi 的 TypeScript 版本）。
