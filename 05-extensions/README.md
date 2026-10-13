# 第 5 章：扩展机制

## 核心概念

前四章已经有了模型调用、文件工具、会话存储和压缩。现在遇到一个新的问题：每增加一种行为，都要修改 Agent 主循环。

例如，我们想记录工具调用、禁止某类命令、增加一个查看项目的工具，或者在压缩前保存快照。把这些需求不断塞进 `prompt()`，主循环很快就变成业务逻辑的集合。

本章把“什么时候允许改变行为”变成明确的接口：

| 能力 | 解决的问题 | 本章实现 |
|---|---|---|
| 工具注册 | 模型怎样知道并调用新能力？ | `ToolRegistry` |
| 事件钩子 | 执行前后怎样插入逻辑？ | `ExtensionAPI.on()` |
| 用户命令 | 怎样增加 `/project`，而不调用模型？ | `CommandRegistry` |
| 扩展加载 | 怎样把这些行为放进单独文件？ | Python 文件中的 `setup(api)` |

本章使用同步 Python，把工具串行执行；不引入异步调度、热重载和扩展包管理。pi 的源码对照固定在提交 [`8bb180bec133199c913ca8ef9bb30bd9ab12a72b`](https://github.com/earendil-works/pi/tree/8bb180bec133199c913ca8ef9bb30bd9ab12a72b)，不要把本章简化的事件名当成 pi 的实际 API。

## 先把前四章接起来

第 4 章为了独立演示，直接使用 OpenAI 消息格式和 bash，没有接入第 2 章的 Provider、第 3 章的文件工具。本章先解决这个衔接问题。

```
第 2 章 Message / Provider ──── 模型调用
                                  │
第 3 章 read/write/edit/bash ── Agent ── 第 5 章扩展
                                  │
第 4 章 Session / Compaction ── 会话存储与摘要
```

我们复用已有实现，不再复制一份文件工具和压缩算法：

- `previous.py` 把已有章节作为不同的 Python 包加载。
- 第 2 章的 `Message`、`ToolCall`、`Provider` 移到 `messages.py`；原来的入口和 `from main import Message` 仍然可用。
- `providers.py` 负责统一消息与会话记录之间的转换。
- `SummaryClient` 把摘要请求送给当前 Provider，复用第 4 章的切点、提示词和上下文投影。

为什么需要单独处理导入？各章都叫 `main.py`，直接把所有 `src/` 加进 `sys.path` 后再 `import main`，Python 可能拿到另一章已缓存的模块。`previous.py` 给每章一个独立包名，让这些模块有不同的身份。

### 模型消息与存储记录是两个边界

模型层使用第 2 章的结构：

```python
ToolCall(id="c1", name="read", arguments={"path": "README.md"})
```

第 4 章的 JSONL 文件仍使用可序列化的字典：

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [{
    "id": "c1",
    "type": "function",
    "function": {"name": "read", "arguments": "{\"path\": \"README.md\"}"}
  }]
}
```

`to_record()` 在写入会话时转换，`to_message()` 在调用 Provider 前转换。这让存储格式保持兼容，而 Agent 不必根据厂商区分工具结果的角色和内容块。

接入 Anthropic 时，还需要把 system 消息作为独立请求参数，并把同一批工具结果合并进一个后继 user 消息。接入摘要时，Provider 必须接收 `max_tokens`，而不能悄悄忽略第 4 章设置的摘要预算。对应修正在第 2 章适配器中，测试使用假的 SDK 响应。

## 文件结构

```
05-extensions/
├── src/
│   ├── previous.py          # 导入前面章节的实现
│   ├── providers.py         # 消息转换、摘要桥接、离线 Provider
│   ├── registry.py          # 工具和命令注册表
│   ├── extensions.py        # 事件与扩展文件加载
│   ├── agent.py             # 接入扩展的 Agent 循环
│   ├── commands.py          # /help、/compact、/tree 等
│   ├── main.py              # 本章演示入口
│   └── test_extensions.py   # 假模型集成测试
├── examples/
│   ├── log_calls.py         # 记录工具名称与结果长度
│   ├── guard_bash.py        # 演示拦截工具调用
│   └── project_info.py      # 自定义工具与 /project 命令
├── requirements.txt
└── README.md
```

这些文件依赖完整仓库中的第 2～4 章。每章有自己的运行入口；“独立运行”不代表只复制本章目录就能运行。

## 第一部分：把工具变成注册表

### 工具定义和执行器必须一起注册

只把新工具的 JSON Schema 发给模型是不够的；模型调用它后，程序还要找到真正的执行函数。

一个注册项包含两部分：

```python
api.register_tool(
    {
        "name": "echo",
        "description": "回显一段文字",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    lambda args: args["text"],
)
```

`definitions()` 返回工具定义的副本。Agent 在每次调用模型时读取它，因此扩展注册的新工具会出现在下一次请求中。

`execute(name, arguments)` 根据名称查找执行器。工具名称重复时直接报错，避免两个扩展无意间覆盖彼此，或把内置 `write` 替换成含义不同的工具。

### 注册时和执行时检查不同的事情

注册时检查名称、Schema 是否合法、执行器是否可调用。执行时用 `jsonschema` 验证模型实际生成的参数。

例如，模型发送下面的参数：

```json
{"text": 123}
```

因为 `text` 应该是字符串，执行器不会被调用；模型得到一条“参数验证失败”的工具结果，可以在下一轮纠正。

未知工具、参数错误和执行异常都转成字符串结果，不会因为一个工具失败就丢失整批消息。用户中断的 `KeyboardInterrupt` 则向上传递，让 Agent 和 CLI 完成清理。

本教程统一要求工具返回字符串。图片、多种内容块、结构化结果和独立的 `is_error` 字段属于后续可以扩展的类型设计。

### 内置工具也走同一条路径

`builtin_tools()` 把第 3 章的四个工具注册进来。之后 Agent 不再包含：

```python
if name == "bash":
    ...
elif name == "read":
    ...
```

它只调用注册表。`read` 和扩展注册的 `project_info` 因此拥有相同的验证、钩子和历史记录路径。

## 第二部分：生命周期钩子

### 钩子放在哪里？

```
保存 user 消息
    ↓
turn_start
    ↓
检查压缩 → before_compact → 压缩 → after_compact
    ↓
调用 Provider
    ↓
保存完整 assistant 消息
    ↓
before_tool_call → 验证参数 → 执行工具 → after_tool_call
    ↓
保存与调用 id 配对的 tool 消息
    ↓
继续调用 Provider，或 turn_end
```

本章的一个 turn 指一次用户请求及其完整的工具循环。pi 对 turn 的划分和事件字段更丰富，本章没有逐项复刻。

| 本章事件 | 参数 | 允许返回的决定 |
|---|---|---|
| `turn_start` | 用户请求 | 观察，不改变主循环 |
| `before_tool_call` | id、名称、参数 | `ToolDecision`：拦截或替换参数 |
| `after_tool_call` | 实际参数、结果字符串 | `ResultDecision`：替换结果 |
| `before_compact` | 自定义关注点 | `CompactDecision`：取消压缩 |
| `after_compact` | 压缩检查点及前后 token 数 | 观察 |
| `turn_end` | completed / max_steps / error、步数 | 观察 |

`after_tool_call` 只在通过前置钩子并进入工具执行路径后触发。被拦截的调用依然会产生 tool 消息和面向 UI 的 `tool_end` 事件，但不会触发这个后置钩子。

### 用显式返回值表达决定

拦截一个工具调用：

```python
from extensions import ToolDecision

def before(event, agent):
    if event["name"] == "bash" and "rm " in event["arguments"].get("command", ""):
        return ToolDecision(block=True, reason="示例扩展禁止包含 'rm ' 的命令")

api.on("before_tool_call", before)
```

返回 `None` 表示继续。返回 `ToolDecision(arguments=...)` 可以替换参数，替换后的参数仍要经过 Schema 验证。

每个处理器收到事件数据的深拷贝。修改 `event["arguments"]` 不会偷偷改变实际调用，只有显式返回决定才生效。事件数据的隔离有助于理解行为，但扩展仍拿得到 Agent 对象，不能把它当成权限隔离。

### 多个扩展怎么组合？

按注册顺序执行。前置钩子返回 block 后立即结束，本次工具不执行；后面的前置钩子也不执行。

后置钩子按顺序改写结果。例如 A 在结果后加 `-a`，B 再加 `-b`，结果成为 `原结果-a-b`。B 看见的是 A 修改后的字符串，而不是最初的结果。

宿主在开始分发时固定处理器列表。处理器运行中注册的新钩子，到下一次同类事件才生效，不会把当前分发变成一个无限增长的循环。

### 扩展异常不能一律“忽略后继续”

观察事件出错时，报告错误后继续。日志扩展失败，不应该把已经成功执行的文件读取变成失败。

前置决策钩子出错时，阻止当前操作。假设检查规则加载失败，直接继续执行命令会绕过本来应该进行的检查。

| 出错位置 | 本章处理 |
|---|---|
| `turn_start`、`turn_end`、`after_compact` | 报告错误，继续 |
| `before_tool_call` | 不执行工具，返回错误结果 |
| `after_tool_call` | 保留最近一次有效结果，报告错误 |
| `before_compact` | 不进行压缩，报告错误 |
| 加载扩展文件或执行 `setup()` | 停止启动，避免继续使用部分注册的宿主 |

取消自动压缩不会让上下文凭空变小。若模型随后报告上下文超限，恢复压缩也被取消，本轮会返回错误，而不会无限重试。

## 第三部分：被拦截，也要保存结果

这是扩展机制最容易遗漏的地方。

模型已经返回了 `tool_call_id="c1"`。即使扩展阻止执行，历史里也必须有对应的工具结果：

```json
{
  "role": "tool",
  "tool_call_id": "c1",
  "content": "错误: 工具调用被扩展拦截: 示例扩展禁止包含 'rm ' 的命令"
}
```

拦截是“用错误结果替代执行”，不能直接 `continue` 跳过消息。否则下一次请求可能因为工具调用与结果不配对而被 API 拒绝。

模型输出因 token 上限被截断时，也不能执行可能不完整的工具调用。适配器保留 `stop_reason`，Agent 在 `length` 或 `max_tokens` 时补充“工具未执行”的结果，再结束本轮并报告错误。

### 中断后为什么不自动重试工具？

假设 `write` 已写完文件，但进程在保存结果之前退出。仅凭 JSONL 文件中的未完成调用，无法知道工具是“完全没执行”还是“已经产生副作用”。自动重放可能覆盖文件、重复提交或重复发送请求。

本章采用保守且可解释的恢复方式：找到末尾未配对的调用，追加合成错误结果，提示用户先检查实际状态，不执行这些工具。

如果一批工具有两个调用，第一个执行时被中断，第二个尚未开始，两个 id 都会补齐结果。CLI 可以继续接收新请求，下一次模型请求拥有完整的消息协议。

这不实现文件回滚，也不能保证写文件的原子性；这里恢复的是**对话结构**。JSONL 中间已有损坏的调用顺序时，程序报错，不猜测应该怎样重排历史。

## 第四部分：命令属于用户，工具属于模型

`project_info` 工具被放在请求中，由模型选择何时调用。`/project` 命令由用户明确输入，程序立即执行，不发送给模型。

```python
from registry import CommandResult

api.register_command(
    "project",
    "查看项目目录，不调用模型",
    lambda args, agent: CommandResult("这里返回项目说明"),
)
```

处理器收到斜杠命令后面的原始字符串。本章只做一次空格分割，不解析引号、子命令和多个选项；命令需要复杂参数时，可以自行用 `shlex.split()` 或 `argparse`。

`CommandResult` 包含内容、是否退出、是否为错误。内置 `/help`、`/quit`、`/tree`、`/tokens`、`/compact`、`/branch` 也在同一个注册表中。

命令输入不会作为 user 消息进入模型历史。未知命令会直接提示 `/help`，不会把拼写错误交给模型猜测。`/compact` 是例外：命令本身不进入历史，但它需要调用模型生成摘要，并保存压缩检查点。

`/branch` 会拒绝落在未完成工具批次中间的位置，避免切出一个缺少工具结果的上下文。它仍只改变当前对话分支，不恢复磁盘文件，也不恢复 Git 分支。

## 第五部分：加载扩展文件

扩展是一个普通 Python 文件，导出 `setup(api)`：

```python
def setup(api):
    api.on("before_tool_call", before)
    api.register_tool(definition, execute)
    api.register_command("hello", "打招呼", handler)
```

宿主用 `importlib` 加载文件，然后调用 `setup()`。加载阶段通常只注册行为；真正的文件读取和命令执行应放在工具或命令处理器中。

示例入口默认加载三个附带扩展，也可以用 `-e path/to/extension.py` 加载自己的文件。不扫描当前项目中的任意 Python 文件，不实现重载、依赖安装或扩展卸载。

Python 扩展与宿主运行在同一进程，拥有相同的文件和网络权限。附带的 `guard_bash.py` 只是展示钩子：`rm` 的别名、脚本、参数变形和其他删除方式都可能绕过字符串匹配，它不是沙箱，也不是完整的权限策略。

## 对比 pi 的真实实现（TypeScript）

### 1. 注册表与扩展宿主

pi 的 [`loader.ts`，L289–318](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/loader.ts#L289-L318) 提供 `registerTool()` 和 `registerCommand()`，把定义与扩展来源记录进运行时；注册工具之后会刷新工具列表。

TypeScript 中的 `Map` 类似 Python 的字典，`handler: (...) => Promise<...>` 表示一个可能返回异步结果的处理器。我们的同步版本直接调用 Python 函数，不实现 pi 的运行时重绑定和异步生命周期。

### 2. 前置拦截与后置改写

pi 的 [`runner.ts`，L1242–1259](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/runner.ts#L1242-L1259) 按顺序等待 `tool_call` 处理器，遇到 block 即返回。对应的是本章 `before_tool_call`，名字不同，核心控制流相似。

[`emitToolResult()`，L1183–1240](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/runner.ts#L1183-L1240) 让处理器依次修改当前结果。pi 的结果还能包含内容块、details、structuredContent、isError 和 usage；本章只处理一个字符串。

`await handler(...)` 表示等待扩展完成；慢处理器会延长实际执行时间。`undefined` 可以理解成“没有返回改写决定”，相当于这里的 `None`。

### 3. 压缩前的事件

pi 的 [`SessionBeforeCompactEvent`，L770–782](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/types.ts#L770-L782) 带有压缩准备数据、分支 entries、触发原因、是否重试，以及 `AbortSignal`。

本章 `before_compact` 只传关注点，允许取消。没有实现扩展提供完整自定义摘要，也没有把触发原因、取消信号和完整准备数据全部暴露出去。这是练习可以继续推进的方向。

### 4. 注册工具不等于激活所有工具

pi 的 [扩展文档：工具暴露与激活](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/extensions.md#L152-L184) 区分注册工具、模型可见工具和其他工具能够调用的工具。

本章注册后直接把所有定义发给模型。它适合少量工具的教程；真实系统中工具越来越多时，应该考虑激活集合、延迟发现和 Schema 带来的上下文开销。

| 特性 | mini-pi 本章 | pi 参考提交 |
|---|---|---|
| 扩展语言 | Python | TypeScript / JavaScript |
| 工具、命令、事件 | 有 | 有，接口和类型更丰富 |
| 工具参数验证 | JSON Schema | 有工具参数验证流程 |
| 前置拦截 | 有；处理器异常也不执行 | 有；处理器失败阻止工具 |
| 结果组合改写 | 字符串，按顺序 | 多种结果字段，按顺序 |
| 压缩前扩展 | 取消 | 取消或定制压缩 |
| 工具集合 | 注册后全部可见 | 暴露策略、激活集合、动态发现 |
| 生命周期与 UI | 同步函数 | 异步、会话上下文和终端 UI 能力 |
| 重载与分发 | 未实现 | 扩展重载、包和发现机制 |

关于错误处理和加载方式，可继续阅读 [pi 扩展文档](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/extensions.md)。

## 如何运行

所有命令都在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r 05-extensions/requirements.txt

# 不需要 API key，使用确定性的假模型
python3 05-extensions/src/main.py --demo "介绍这个项目"

# 交互模式，输入 /project、/help 或普通任务
python3 05-extensions/src/main.py --demo
```

离线演示总是先调用项目查看工具，再输出结果，不会理解用户的任意任务，也不生成真实摘要。

连接模型：

```bash
export OPENAI_API_KEY="your-api-key"
python3 05-extensions/src/main.py "先读取 README.md，再概括项目结构"

# Anthropic 模型名由你明确提供
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_MODEL="your-model-id"
python3 05-extensions/src/main.py --provider anthropic "读取 README.md"

# 自定义扩展
python3 05-extensions/src/main.py -e /path/to/hello.py
```

本章演示入口会创建新会话，文件位于 `~/.mini-pi/chapter05/`。恢复、输入历史和流式终端展示在第 6 章统一实现。

## 测试（无需 API key）

```bash
python3 -m pytest 05-extensions/src/test_extensions.py -v
```

测试验证真实主循环，模型使用假的 Provider，覆盖动态工具传递、参数验证、拦截与配对、扩展异常、参数和结果改写、命令不调用模型、压缩取消、当前 Provider 生成摘要、阈值和溢出压缩、中断与恢复、输出截断、流式响应、文件读写和分支边界。

第 2 章新增适配器测试，验证摘要预算、Anthropic system 和批量工具结果、OpenAI 流关闭和结束标记。所有章节的测试运行方式见根目录 README。

## 练习

### 练习 1：压缩前保存上下文快照

注册 `before_compact`，把 `agent.context_records()` 写入一个单独的 JSON 文件。文件名包含时间和当前 leaf id，避免覆盖先前快照。

验证：手动 `/compact` 后，快照仍包含压缩前的完整上下文；快照写入失败时，压缩不会继续。不要把快照当成普通会话消息追加，否则会让待压缩的上下文继续增长。

### 练习 2：启用和停用扩展

给每个注册项记录来源，用 `/extensions` 查看状态，用 `/disable <name>` 停用该扩展的处理器和工具。

验证：停用之后新请求不再声明它的工具；已保存的工具结果仍能恢复。内置命令不能被扩展覆盖；停用发生在一个工具批次结束后，避免模型刚请求的工具突然消失。

## 下一步

现在核心循环允许扩展行为，前四章的能力也已接回同一个 Agent。第 6 章将复用这些模块，把命令行参数、流式输出、会话恢复、输入历史和中断处理组织成完整的使用体验。
