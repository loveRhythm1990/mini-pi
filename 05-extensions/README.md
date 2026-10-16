# 第 5 章：扩展机制

## 核心概念

前四章已经有了模型调用、文件工具、会话存储和压缩。现在遇到一个新的问题：每增加一种行为，都要修改 Agent 主循环。

例如，我们想记录工具调用、禁止某类命令、增加一个查看项目的工具，或者在压缩前保存快照。把这些需求不断塞进 `prompt()`，主循环很快就变成业务逻辑的集合。

本章把"什么时候允许改变行为"变成明确的接口。扩展（extension）是一个普通文件，里面声明"注册什么"；宿主（Agent 程序）在固定的时机加载它、分发事件、执行注册的能力：

| 能力 | 解决的问题 | 本章实现 |
|---|---|---|
| 工具注册 | 模型怎样知道并调用新能力？ | `ToolRegistry` |
| 用户命令 | 怎样增加 `/project`，而不调用模型？ | `CommandRegistry` |
| 事件钩子 | 执行前后怎样插入逻辑？ | `ExtensionAPI.on()` |
| 扩展加载 | 怎样把这些行为放进单独文件？ | Python 文件中的 `setup(api)` |

**阅读路线**：第一部分从使用开始——安装、体验，以及 pi 的真实安装版图；第二、三部分实现两个入口（工具给模型、命令给用户）；第四部分讲钩子，第五部分处理钩子带来的协议问题（拦截、截断、中断）；第六部分看宿主如何组装这一切；第七部分把运行时提炼为生产者-消费者模型，对照 pi 的消息队列；最后是与 pi 实现的逐项对照、运行方式与练习。

本章使用同步 Python，把工具串行执行；不引入异步调度、热重载和扩展包管理——用户如何安装配置在第一部分、消息如何排队在第七部分对照 pi 展开。pi 的源码对照固定在提交 [`8bb180bec133199c913ca8ef9bb30bd9ab12a72b`](https://github.com/earendil-works/pi/tree/8bb180bec133199c913ca8ef9bb30bd9ab12a72b)，不要把本章简化的事件名当成 pi 的实际 API。

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

这些文件依赖完整仓库中的第 2～4 章。每章有自己的运行入口；"独立运行"不代表只复制本章目录就能运行。

## 第一部分：从使用开始

机制留到后面，先回答使用者的三个问题：怎么装、装完会怎样、pi 里是什么样。

### 安装：把文件交给宿主

注册写在扩展文件里，由宿主在加载时执行；使用者的动作只有一个——把文件交给宿主：

```bash
# mini-pi：用 -e 指定扩展文件，可重复使用
python3 05-extensions/src/main.py -e ./my_ext.py

# pi：放进扩展目录后自动加载，或临时指定
#   ~/.pi/agent/extensions/my_ext.ts（所有项目生效）
#   pi --extension ./my_ext.ts
```

扩展文件可以自己编写（第二部分就教），也可以使用别人写好的，宿主不做区分：加载时逐条执行文件里的 `api.register_tool(...)`，工具随之进入注册表。附带的三个示例扩展由入口自动加载，无需任何参数。

### 装好之后：变化在工具箱，不在界面

安装之后，界面上没有任何变化：提示符相同，`/help` 也不多一行（`/project` 除外——那是命令，见第三部分）。变化发生在模型的工具箱里。一次典型的交互：

```
> 这个项目的目录里都有什么？
[扩展日志] 即将调用 project_info           ← log_calls 的输出（stderr）
工作目录：/Users/you/mini-pi
05-extensions/
06-skills/
07-cli/
...
README.md
[扩展日志] project_info 返回 512 字符
这个项目是一个中文教程，包含八个章节……      ← 模型基于工具结果给出回答
```

三个要点：

1. **工具由模型调用**。不存在"执行 project_info"这样的操作；使用者用自然语言描述任务，模型自行决定是否使用工具。希望模型更常用某个工具，依靠的是提示词和工具描述，而不是菜单
2. **验证安装的方法是派任务**。给模型一件需要该工具的事，观察它是否被调用；`log_calls` 输出到 stderr 的日志正好是观察窗口。本章没有列出工具清单的命令
3. **工具定义占用上下文**。`definitions()` 的内容作为 `tools` 参数出现在每一次请求中，消耗 token。这是 pi 区分"注册"与"激活"的原因（对比部分第 4 节）：注册一百个、只激活十个，工具定义才不会吃掉预算

### pi 的安装版图

pi 的扩展从三个来源进入，用户不用改任何代码：

| 来源 | 位置 | 适合 |
|---|---|---|
| 用户级 | `~/.pi/agent/extensions/` | 跟着人走，所有项目生效 |
| 项目级 | 仓库里的 `.pi/extensions/` | 跟着仓库走，团队共享（提交进 Git） |
| 命令行 | `pi --extension ./hello.ts` | 开发调试，一次性 |

发现规则（[`loader.ts` L782-787](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/loader.ts#L782-L787)）：目录下的直接 `.ts` / `.js` 文件、带 `index.ts` / `index.js` 的子目录、带 `package.json` 且声明了 `"pi"` 字段的子目录，都会被加载。

开发循环很顺：写一个 TypeScript 文件，`pi --extension` 加载，不需要编译步骤（pi 用 `jiti` 直接执行 TS）；改完用 `/reload` 整体重载——重载会替换整个扩展运行时，`await ctx.reload()` 之后的代码不能复用旧运行时的状态。分发则通过 [Pi packages](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/packages.md)。

**项目扩展要过信任关**。扩展与宿主同进程、同权限（能读文件、网络和凭据），所以项目级扩展在首次打开时会触发信任确认（[`project-trust.ts` L47](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/project-trust.ts#L47) 的 `resolveProjectTrusted`）：用户可以选择记住决定；`defaultProjectTrust` 设置能把它固化成 `ask`（默认）/ `always` / `never`；个人级和命令行扩展甚至可以通过 `project_trust` 事件替用户自动作答。这层设计对应一个现实威胁：clone 一个陌生仓库时，`.pi/extensions/` 里的代码不能自动获得执行权。

mini-pi 的安装没有这么多层次：`-e` 显式指定、附带示例自动加载，仅此而已。差距是刻意的——教学实现里，所有加载行为都发生在明处。

## 第二部分：把工具变成注册表

使用体验背后，第一个要回答的机制问题是：一个新工具如何进入模型的工具箱，并且在被调用时找到真正的执行函数？

### 定义与执行器必须成对

只把新工具的 JSON Schema 发给模型是不够的；模型调用它后，程序还要找到真正的执行函数。一个注册项因此包含两部分——**定义是给模型看的说明书，执行器是兑现说明书的函数**：

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

`definitions()` 返回工具定义的副本。Agent 在每次调用模型时读取它，因此扩展注册的新工具会出现在下一次请求中。`execute(name, arguments)` 根据名称查找执行器。工具名称重复时直接报错，避免两个扩展无意间覆盖彼此，或把内置 `write` 替换成含义不同的工具。

### 注册时检查定义，执行时检查参数

两道检查在不同时间点发生。注册时检查名称、Schema 是否合法、执行器是否可调用；执行时用 `jsonschema` 验证模型实际生成的参数——例如模型发送 `{"text": 123}`，因为 `text` 应该是字符串，执行器不会被调用，模型得到一条"参数验证失败"的工具结果，可以在下一轮纠正。

未知工具、参数错误和执行异常都转成字符串结果，不会因为一个工具失败就丢失整批消息。用户中断的 `KeyboardInterrupt` 则向上传递，让 Agent 和 CLI 完成清理（第五部分展开）。

本教程统一要求工具返回字符串。图片、多种内容块、结构化结果和独立的 `is_error` 字段属于后续可以扩展的类型设计。

### 内置工具走同一条路径

`builtin_tools()` 把第 3 章的四个工具注册进来。之后 Agent 不再包含：

```python
if name == "bash":
    ...
elif name == "read":
    ...
```

它只调用注册表。`read` 和扩展注册的 `project_info` 因此拥有相同的验证、钩子和历史记录路径——扩展拿到的不是"外挂待遇"，而是与内置能力完全一致的基础设施。

## 第三部分：命令属于用户，工具属于模型

工具体现的是"模型的能力"；另一类入口服务于用户自己。`project_info` 工具被放在请求中，由模型选择何时调用；`/project` 命令由用户明确输入，程序立即执行，不发送给模型：

```python
from registry import CommandResult

api.register_command(
    "project",
    "查看项目目录，不调用模型",
    lambda args, agent: CommandResult("这里返回项目说明"),
)
```

处理器收到斜杠命令后面的原始字符串。本章只做一次空格分割，不解析引号、子命令和多个选项；命令需要复杂参数时，可以自行用 `shlex.split()` 或 `argparse`。

`CommandResult` 包含内容、是否退出、是否为错误。内置 `/help`、`/quit`、`/tree`、`/tokens`、`/compact`、`/branch` 也在同一个注册表中——内置命令和扩展命令没有区别。

命令输入不会作为 user 消息进入模型历史。未知命令会直接提示 `/help`，不会把拼写错误交给模型猜测。`/compact` 是例外：命令本身不进入历史，但它需要调用模型生成摘要，并保存压缩检查点。

`/branch` 会拒绝落在未完成工具批次中间的位置，避免切出一个缺少工具结果的上下文（原因见第五部分）。它仍只改变当前对话分支，不恢复磁盘文件，也不恢复 Git 分支。

## 第四部分：生命周期钩子

工具和命令解决"能力从哪来"；钩子解决"什么时候插入逻辑"。记录每次调用、拦截危险命令、在压缩前保存快照——这些需求都不新增能力，只要求在主循环的固定节拍上挂一段代码。

### 钩子在主循环的哪里

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

### 多个扩展的组合

按注册顺序执行。前置钩子返回 block 后立即结束，本次工具不执行；后面的前置钩子也不执行。后置钩子按顺序改写结果：例如 A 在结果后加 `-a`，B 再加 `-b`，结果成为 `原结果-a-b`——B 看见的是 A 修改后的字符串，而不是最初的结果。

宿主在开始分发时固定处理器列表。处理器运行中注册的新钩子，到下一次同类事件才生效，不会把当前分发变成一个无限增长的循环。

### 扩展异常不能一律"忽略后继续"

观察事件出错时，报告错误后继续——日志扩展失败，不应该把已经成功执行的文件读取变成失败。前置决策钩子出错时则阻止当前操作：检查规则加载失败还继续执行命令，等于绕过了本该进行的检查。

| 出错位置 | 本章处理 |
|---|---|
| `turn_start`、`turn_end`、`after_compact` | 报告错误，继续 |
| `before_tool_call` | 不执行工具，返回错误结果 |
| `after_tool_call` | 保留最近一次有效结果，报告错误 |
| `before_compact` | 不进行压缩，报告错误 |
| 加载扩展文件或执行 `setup()` | 停止启动，避免继续使用部分注册的宿主 |

取消自动压缩不会让上下文凭空变小。若模型随后报告上下文超限，恢复压缩也被取消，本轮会返回错误，而不会无限重试。

## 第五部分：拦截、截断与中断下的协议完整性

钩子让扩展可以拦下调用，但拦截本身会制造协议问题。这一部分处理三种"半途而废"的情况，它们的答案共同指向第 4 章建立的规则：**每个 toolCall 必须有配对的 toolResult**。

### 被拦截，也要保存结果

这是扩展机制最容易遗漏的地方。模型已经返回了 `tool_call_id="c1"`，即使扩展阻止执行，历史里也必须有对应的工具结果：

```json
{
  "role": "tool",
  "tool_call_id": "c1",
  "content": "错误: 工具调用被扩展拦截: 示例扩展禁止包含 'rm ' 的命令"
}
```

拦截是"用错误结果替代执行"，不能直接 `continue` 跳过消息。否则下一次请求可能因为工具调用与结果不配对而被 API 拒绝。

### 截断的输出不能执行

模型输出因 token 上限被截断时，也不能执行可能不完整的工具调用。适配器保留 `stop_reason`，Agent 在 `length` 或 `max_tokens` 时补充"工具未执行"的结果，再结束本轮并报告错误——截断的参数可能解析成功但悄悄缺失内容，执行它比报错危险得多。

### 中断后为什么不自动重试工具

假设 `write` 已写完文件，但进程在保存结果之前退出。仅凭 JSONL 文件中的未完成调用，无法知道工具是"完全没执行"还是"已经产生副作用"。自动重放可能覆盖文件、重复提交或重复发送请求。

本章采用保守且可解释的恢复方式：找到末尾未配对的调用，追加合成错误结果，提示用户先检查实际状态，不执行这些工具。如果一批工具有两个调用，第一个执行时被中断，第二个尚未开始，两个 id 都会补齐结果——CLI 可以继续接收新请求，下一次模型请求拥有完整的消息协议。

这不实现文件回滚，也不能保证写文件的原子性；这里恢复的是**对话结构**。JSONL 中间已有损坏的调用顺序时，程序报错，不猜测应该怎样重排历史。

## 第六部分：宿主的组装

机制都有了：注册表、钩子、两条入口。最后看宿主怎么把它们装配起来——包括加载扩展文件，以及把前四章的实现接进同一个循环。

### 加载扩展文件

扩展是一个普通 Python 文件，导出 `setup(api)`：

```python
def setup(api):
    api.on("before_tool_call", before)
    api.register_tool(definition, execute)
    api.register_command("hello", "打招呼", handler)
```

宿主用 `importlib` 加载文件，然后调用 `setup()`。加载阶段通常只注册行为；真正的文件读取和命令执行应放在工具或命令处理器中。示例入口固定先加载三个附带扩展，再依次加载 `-e` 给出的路径——**加载顺序即钩子顺序**（第四部分的组合规则）。

未实现的部分同样明确：不扫描目录、不支持重载、不能运行中启停（练习 2 补上）、没有包管理。扩展文件必须显式指定。

Python 扩展与宿主运行在同一进程，拥有相同的文件和网络权限。附带的 `guard_bash.py` 只是展示钩子：`rm` 的别名、脚本、参数变形和其他删除方式都可能绕过字符串匹配，它不是沙箱，也不是完整的权限策略。

### 与前四章的接线

第 4 章为了独立演示，直接使用 OpenAI 消息格式和 bash，没有接入第 2 章的 Provider、第 3 章的文件工具。本章把它们接回同一个 Agent：

```
第 2 章 Message / Provider ──── 模型调用
                                  │
第 3 章 read/write/edit/bash ── Agent ── 第 5 章扩展
                                  │
第 4 章 Session / Compaction ── 会话存储与摘要
```

实现上有两个细节值得知道。

**跨章导入**：各章的源文件都叫 `main.py`，直接把所有 `src/` 加进 `sys.path` 再 `import main`，Python 可能拿到另一章已缓存的模块。`previous.py` 给每章一个独立包名，让这些模块有不同的身份。

**两个消息边界**：模型层使用第 2 章的结构（`ToolCall(id="c1", name="read", arguments={...})`），第 4 章的 JSONL 文件使用可序列化的字典（`"function": {"name": ..., "arguments": "{\"path\": ...}"}`）。`to_record()` 在写入会话时转换，`to_message()` 在调用 Provider 前转换——存储格式保持兼容，Agent 不必根据厂商区分工具结果的角色和内容块。接入 Anthropic 时还需要把 system 消息作为独立请求参数、把同一批工具结果合并进一个后继 user 消息；接入摘要时，Provider 必须接收 `max_tokens` 而不能悄悄忽略第 4 章的摘要预算。对应修正在第 2 章适配器中，测试使用假的 SDK 响应。

## 第七部分：生产者与消费者

最后退一步，看运行时的整体图景。这一部分给钩子机制一个更准确的名字，也解释 mini-pi 与 pi 在"忙碌时能不能输入"上的差距从何而来。

### 事件分发的本质：宿主生产，扩展消费

**宿主是事件的生产者，扩展是消费者**。生产发生在固定时机、携带固定数据（第四部分那张流程图就是"生产线"）；消费按注册顺序进行。与直接函数调用相比，方向反了过来——宿主不知道谁在听，新增行为不需要改主循环。这就是控制反转：不是"主循环调用扩展的逻辑"，而是"扩展的逻辑订阅主循环的节拍"。

消费者有两种姿态：

| 姿态 | 事件 | 生产者等它吗 |
|---|---|---|
| 观察（notify） | `turn_start`、`turn_end`、`after_compact`、`after_tool_call` | 分发完就继续，返回值只用于结果改写 |
| 审批（request-response） | `before_tool_call`、`before_compact` | 宿主停下来等决定：执行、拦截、替换参数、取消 |

mini-pi 的分发是**同步背靠背**的：生产一条、消费一条，消费者的耗时直接加在主循环上。pi 也一样——处理器是 `await` 的，官方文档明确提醒"慢处理器会拖慢流式消费"。

### mini-pi 的边界：串行世界

交互循环是这样的：

```python
while True:
    text = input()        # ← 生产输入：必须等 Agent 空闲
    agent.prompt(text)    # ← 消费输入：期间无法接收新的输入
```

`input()` 和 `prompt()` 互相等待——模型工作时用户**无法生产输入**，唯一的例外是 Ctrl+C，而它走的不是队列，是第 4 章的 abort 路径。这种实现简单直接，但体验有代价：长任务执行期间，中途想到的修正只能等待。

### pi 的双队列：steering 与 follow-up

pi 在用户（生产者）和 agent 循环（消费者）之间放了两条队列：[`agent.ts` L143-170](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent.ts#L143-L170) 的 `PendingMessageQueue`（消息数组 + 模式 + `enqueue` / `peek` / `drain`），实例化为 `steeringQueue` 和 `followUpQueue`，分别由 `steer()`（L299）和 `followUp()`（L304）投递：

| 队列 | 入口按键 | 语义 | 消费时机 |
|---|---|---|---|
| steering | 工作中输入 + `Enter` | 调整当前任务 | 当前 assistant 回合（含工具调用）结束后、下一请求前 |
| follow-up | 工作中输入 + `Alt+Enter` | 追加新任务 | 整个 agent run 本来要结束时 |

用户按键一览（[usage.md](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/usage.md#change-direction)）：

| 想做什么 | 按键 |
|---|---|
| 调整正在进行的任务 | 输入后 `Enter` |
| 在当前任务之后追加工作 | 输入后 `Alt+Enter` |
| 把排队的消息拿回编辑器 | `Alt+Up` |
| 停掉当前任务 | `Escape`（排队消息一并退回编辑器） |

时间线上看两个消费点：

```
user
 │
assistant ─┐
tool       │  一个回合 = 一次回复 + 它的工具批次
tool       │
assistant ─┘
 ◆ 回合边界：steering 消费点（agent-loop.ts L295）
   （若队列有消息，先作为新 user 消息进入，再请求模型）
assistant ─┐
tool       │
assistant ─┘
 ◆ 回合边界：steering 消费点
 …
 ◆ run 结束：follow-up 消费点（agent-loop.ts L302）
   （有排队的 follow-up 就再开一个 run；没有则真正空闲）
```

两个设计值得停下来想：

1. **为什么在"回合边界"消费，而不是立刻？** 模型的回复是流式的，工具调用与结果必须配对（第 4 章）——把用户消息插进 assistant 消息和它的工具结果中间会破坏协议，插进一条流式回复的中间则谁都读不懂。回合边界是"语义完整"的最小单位；队列把"随时生产"翻译成了"在安全点消费"
2. **默认一次只喂一条。** 队列模式 `one-at-a-time`（默认，`settings.json` 的 `steeringMode` / `followUpMode`，可改 `all`）：每回合只送出队首那条。用户排队三条补充，模型每轮先消化第一条、给出反应，再收下一条——避免三条一起砸进去，模型只回应最后一条、前两条被淹没

abort 的处理也顺理成章：`Escape` 中止时，队列里没消费的消息全部退回编辑器（[interactive-mode.ts L3072](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L3072)）——没说出口的话，不该凭空消失。

mini-pi 未实现队列：它依赖的并发（线程或事件循环）会让本章的每个示例都要先处理锁。练习 3 给出了最小可行版本。

## 对比 pi 的真实实现（TypeScript）

### 1. 注册表与扩展宿主

pi 的 [`loader.ts`，L289–318](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/loader.ts#L289-L318) 提供 `registerTool()` 和 `registerCommand()`，把定义与扩展来源记录进运行时；注册工具之后会刷新工具列表。

TypeScript 中的 `Map` 类似 Python 的字典，`handler: (...) => Promise<...>` 表示一个可能返回异步结果的处理器。我们的同步版本直接调用 Python 函数，不实现 pi 的运行时重绑定和异步生命周期。

### 2. 前置拦截与后置改写

pi 的 [`runner.ts`，L1242–1259](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/runner.ts#L1242-L1259) 按顺序等待 `tool_call` 处理器，遇到 block 即返回。对应的是本章 `before_tool_call`，名字不同，核心控制流相似。

[`emitToolResult()`，L1183–1240](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/runner.ts#L1183-L1240) 让处理器依次修改当前结果。pi 的结果还能包含内容块、details、structuredContent、isError 和 usage；本章只处理一个字符串。

`await handler(...)` 表示等待扩展完成；慢处理器会延长实际执行时间。`undefined` 可以理解成"没有返回改写决定"，相当于这里的 `None`。

### 3. 压缩前的事件

pi 的 [`SessionBeforeCompactEvent`，L770–782](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/extensions/types.ts#L770-L782) 带有压缩准备数据、分支 entries、触发原因、是否重试，以及 `AbortSignal`。

本章 `before_compact` 只传关注点，允许取消。没有实现扩展提供完整自定义摘要，也没有把触发原因、取消信号和完整准备数据全部暴露出去。这是练习可以继续推进的方向。

### 4. 注册工具不等于激活所有工具

pi 的 [扩展文档：工具暴露与激活](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/extensions.md#L152-L184) 区分注册工具、模型可见工具和其他工具能够调用的工具。

本章注册后直接把所有定义发给模型。它适合少量工具的教程；真实系统中工具越来越多时，应该考虑激活集合、延迟发现和 Schema 带来的上下文开销。

### 5. 端到端：官方示例的真实长相

前面的对照都停在 API 层面。换一个视角：把官方示例库（[examples/extensions/](https://github.com/earendil-works/pi/tree/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/examples/extensions)，80 个可运行的扩展）里最贴近本章三个附带示例的几个真实文件搬出来，从"用户放文件"到"看到效果"完整走一遍。

**最小工具：[`hello.ts`](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/examples/extensions/hello.ts)**（对照 `project_info` 的工具部分）

```typescript
// 放进 ~/.pi/agent/extensions/ 即生效，无需编译
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const helloTool = defineTool({
	name: "hello",
	label: "Hello",
	description: "A simple greeting tool",
	parameters: Type.Object({
		name: Type.String({ description: "Name to greet" }),
	}),

	async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
		return {
			content: [{ type: "text", text: `Hello, ${params.name}!` }],
			details: { greeted: params.name },
		};
	},
});

export default function (pi: ExtensionAPI) {
	pi.registerTool(helloTool);
}
```

体验：对 pi 说"用 hello 工具向 world 问好"，模型调用 `hello`，屏幕出现 `Hello, world!`。与 mini-pi 的 `api.register_tool(定义, 执行器)` 一一对应：`defineTool` 的参数就是定义（名称/描述/TypeBox schema），`execute` 就是执行器。区别在结果更丰富：`content` 给模型，`details` 留给渲染与状态重建。

**生产级拦截：[`protected-paths.ts`](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/examples/extensions/protected-paths.ts)**（对照 `guard_bash`）

```typescript
// 节选，全文仅 30 余行
export default function (pi: ExtensionAPI) {
	const protectedPaths = [".env", ".git/", "node_modules/"];

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName !== "write" && event.toolName !== "edit") {
			return undefined;
		}

		const path = event.input.path as string;
		const isProtected = protectedPaths.some((p) => path.includes(p));

		if (isProtected) {
			if (ctx.hasUI) {
				ctx.ui.notify(`Blocked write to protected path: ${path}`, "warning");
			}
			return { block: true, reason: `Path "${path}" is protected` };
		}

		return undefined;
	});
}
```

与 `guard_bash` 的字符串匹配同一个思路，三处升级：事件名是 `tool_call`（对应我们的 `before_tool_call`）；拦截时**还在 UI 上发一条警告**（`ctx.ui.notify`）；返回 `{ block, reason }` 对应 `ToolDecision(block=True, reason=...)`。被 block 的调用同样会收到错误结果，配对协议与第五部分一致。同一目录的 [`confirm-destructive.ts`](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/examples/extensions/confirm-destructive.ts) 演示了取消型决定：拦截"清空/切换会话"这类操作时用 `ctx.ui.confirm` 弹确认框，用户拒绝就 `return { cancel: true }`——决策类型从"拦工具"扩展到了"拦会话操作"。

**分支正确的状态：[`todo.ts`](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/examples/extensions/todo.ts) 的设计**

todo.ts 注册一个 `todo` 工具和 `/todos` 命令，最有教学价值的是它的状态存放位置：不写文件、不存全局变量，而是放在**工具结果的 `details` 字段**里。于是状态自动跟随会话树——`/branch` 回到历史某点时，todo 列表就是那个时点的列表，不需要任何额外代码。这是第 4 章"分支"概念与扩展状态的天然衔接：**跟随分支的状态就应该住在分支上**（entry），而不是活在进程或磁盘的别处。

三个示例的共同点值得强调：**没有任何一个要求碰 pi 的源码**。写文件、放目录、聊天——与 mini-pi 的 `-e` 是同一条流水线，只是 pi 的目录发现、TypeBox 类型、结果结构和 UI 能力都更完整。

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
| 安装与发现 | `-e` 显式加载 + 附带示例 | 用户/项目目录自动发现、`--extension`、包分发、`/reload` |
| 忙碌时的输入 | 串行等待（input 与 prompt 互斥） | steering / follow-up 双队列，回合边界消费，abort 退回编辑器 |

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

### 试用三个附带扩展

| 附带扩展 | 演示什么 | 怎么体验 |
|---|---|---|
| `project_info.py` | 同时注册一个模型工具和一个 `/project` 命令 | 交互模式输入 `/project`（不调模型，第三部分）；或让模型自己调 `project_info` 工具 |
| `log_calls.py` | 观察型钩子：`before_tool_call` / `after_tool_call` 记日志 | 正常使用工具；日志走 stderr（`api.report`），不混入模型输入输出 |
| `guard_bash.py` | 决策型钩子：拦截包含 `rm ` 的 bash 调用 | 需要真实模型：让它"执行 rm -rf /tmp/test"，模型会收到拦截错误并改道 |

拦截演示需要真实模型：

```bash
export OPENAI_API_KEY="your-api-key"
python3 05-extensions/src/main.py "先读取 README.md，再概括项目结构"

# Anthropic 模型名需显式提供
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_MODEL="your-model-id"
python3 05-extensions/src/main.py --provider anthropic "读取 README.md"

# 自定义扩展
python3 05-extensions/src/main.py -e /path/to/hello.py
```

本章演示入口会创建新会话，文件位于 `~/.mini-pi/chapter05/`。恢复、输入历史和流式终端展示在第 7 章统一实现。

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

### 练习 3：最简 steering 队列

用 `threading` 和 `queue.Queue` 给本章入口加一条 steering 队列：主线程跑 `input()` 生产消息，Agent 在独立线程消费；消费点放在"下一次调用 Provider 之前"（`prompt()` 工具循环的每轮开头），与 pi 的回合边界一致。

验证：任务执行中输入补充说明，模型在下一个回合看到它；Ctrl+C 中止时，队列里未消费的消息退回提示。注意两点：`ExtensionAPI.results` 的事件分发不是线程安全的，跨线程调用需要加锁或改回主线程执行；消费点不要落在工具批次中间，否则破坏第 4 章的配对协议。

## 下一步

现在核心循环允许扩展行为，前四章的能力也已接回同一个 Agent。第 6 章将实现 Skill 系统——与扩展并列的另一条定制通道：扩展添加常驻注册的行为，Skill 按需装载指令。第 7 章再把这些模块组织成完整的 CLI 体验。
