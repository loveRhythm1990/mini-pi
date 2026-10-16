# 第 6 章：Skill 系统——按需装载的指令

## 核心概念

第 5 章的扩展机制解决的是"新增**行为**"：注册工具、拦截调用、改写结果——每个工具定义都会出现在每一次请求的 `tools` 参数里，是**常驻的、按请求计费的**。本章解决另一半需求："注入**知识**"。

- 团队的发布流程、某个领域的操作规范、一套 API 的调用要点——它们不需要任何新的可执行行为，只需要在模型碰到相应任务时出现在上下文里
- 把这类内容写成扩展太重，写进系统提示太贵（它们会占据每一次请求的上下文），只写在对话里又活不过一次会话

Skill 是 pi 给出的答案，本章用 ~150 行 Python 实现它的核心：

| 问题 | Skill 的方案 | 关键设计 |
|---|---|---|
| 指令很长，全部放进上下文太贵 | 启动时只把「名字 + 描述」挂进系统提示 | 渐进式披露 |
| 模型怎么知道何时需要全文？ | 描述即路由：模型读到匹配的任务，自己用 `read` 读取 | 复用文件工具 |
| 用户想点名使用 | `/skill <名字> [请求]` 强制加载 | 复用命令注册表 |
| 想只留手动入口 | `disable-model-invocation: true` 不进自动清单 | 三态控制 |

本章对照 pi 的 skills 机制（`docs/skills.md`，[`packages/coding-agent/docs/skills.md`](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/skills.md)，均已在提交 `8bb180b` 核对）。pi 实现的是 [Agent Skills 规范](https://agentskills.io/specification)，与 Claude Code 等实现通用。

## 需求 → 机制的完整版图

把第 5 章结尾的问题问全："cocding agent 的可定制性有几条通道？"pi 的答案是并列的几层，Skill 是其中之一：

| 需求 | 机制 | 形态 | 章节 |
|---|---|---|---|
| 新增可执行行为（工具、拦截、路由） | 扩展 | Python / TypeScript 代码 | 第 5 章 |
| 注入领域知识、操作流程 | **Skill** | Markdown 目录 + 捆绑文件 | 本章 |
| 常用提示词变成命令 | 提示词模板 | Markdown 文件 | pi：`prompts/` |
| 接入外部工具服务 | MCP 服务器 | 配置 + 外部进程 | pi：`mcp.json` |
| 界面外观 | 主题 | 配置文件 | pi：`themes/` |
| 把以上内容**分发给用户** | 包 | 安装清单 | pi：packages |

判断标准一句话：**需要新的"行为"用扩展；需要注入"知识/流程"用 Skill**。二者经常配合：Skill 描述"何时读 formats.md、何时跑 extract.sh"，脚本本身只是普通文件——不需要一行扩展代码。

## 第一部分：渐进式披露

Skill 的全部魔法就在这一张时序图里：

```
启动
 │ 扫描 Skill 目录，解析每个 SKILL.md 的 frontmatter
 │ 把「名字：描述」清单挂进系统提示（每个 Skill 只占两行）
 ▼
用户输入："帮我发个版"                           ← 与 release-check 描述匹配
 │
模型（自主）：任务像是 release-check → 用 read 读它的「完整指令」
 │                                            ← 全文此刻才进入上下文
 ▼
模型按 SKILL.md 的步骤执行（运行捆绑脚本、汇报结果）
```

三个要点：

1. **"加载"不经过任何新机制**。模型读 SKILL.md 用的就是第 3 章的 `read` 工具——Skill 系统里没有"Skill 执行器"，只有发现、校验、清单和一条命令。这也是它实现量小的根本原因
2. **描述就是路由**。模型判断"要不要读全文"的唯一依据是 frontmatter 里的 description，所以规范要求 description 必须同时写清"做什么"和"**何时用**"——写成"帮助处理 PDF"这种话，模型无从路由
3. **成本模型与扩展相反**。扩展的工具定义每次请求都在场（所以 pi 有"注册 ≠ 激活"）；Skill 的全文只在被需要时进场。第 4 章的压缩是给上下文"卸货"，Skill 是"延迟装货"——同一门上下文经济学

第三条加载路径是`/skill <名字> [请求]`：不依赖模型的路由判断，把全文作为一条 user 消息直接注入（复用第 5 章命令注册表；处理器直接调用 `agent.prompt()`，与内置 `/compact` 同款先例）。`disable-model-invocation: true` 则把 Skill 从自动清单里摘掉、只留手动入口——两个开关组合出三种姿态：自动、仅手动、不可用（不放进目录即可）。

## 第二部分：Skill 的规范形态

一个 Skill 是一个**目录**，入口是 `SKILL.md`：`---` 围出的 frontmatter + Markdown 正文，可捆绑任意子目录：

```
release-check/
├── SKILL.md
└── scripts/
    └── check.sh        ← 正文里用相对路径引用，模型以 Skill 目录为基准解析
```

frontmatter 解析认可的字段与校验规则（对照 pi 提交 `8bb180b` 的语义）：

| 字段 | 作用 | 校验 |
|---|---|---|
| `name` | 唯一标识，命令与显示用 | 小写字母、数字和单个连字符，不首尾连字符，≤64 字符 |
| `description` | **路由描述**，进系统提示 | 必填，≤1024 字符；缺失则整个 Skill 不装载 |
| `disable-model-invocation` | `true` 时不进自动清单 | 可选 |
| 其他（license、metadata…） | 属于 Agent Skills 规范 | 容忍并保留，不做处理 |

随 SKILL.md 附带的两条 pi 行为本章同样实现：**损坏的文件或缺 description 的 Skill 跳过并警告，不让一个坏文件拖垮启动**；**重名保留先发现的并警告**，两个目录装了同名 Skill 不会静默覆盖。

## 第三部分：实现 skills.py

全部核心逻辑在一个文件里（`src/skills.py`，~150 行），分四块：

### 发现与校验：`load_skills(directories) -> (skills, warnings)`

```python
for skill_file in sorted(root.rglob(SKILL_FILE)):   # 递归发现
    fields, _ = parse_skill_file(skill_file)        # 损坏 → 跳过并警告
    reason = rejection_reason(fields)               # name/description 校验
    if name in taken:                               # 重名 → 保留先发现者
        warnings.append(...)
```

返回值设计成 `(skills, warnings)` 二元组而不是边扫描边打印：警告的呈现方式交给调用方（本章入口打到 stderr，第 7 章 CLI 走它的 UI 通知）——宿主不应替模块做展示决策。

### frontmatter 解析：`parse_skill_file`

`---` 围出的 `key: value` 行，用 `partition(":")` 解析。不引入 YAML 依赖：正规 frontmatter 是 YAML 子集，但教程实现只需要三个字段，15 行字符串处理就够了——**依赖的取舍本身是教学内容**：解析器的宽容度决定了坏文件是拖垮启动还是被跳过。

### 清单生成：`skills_prompt(skills)`

```python
<skills>
- release-check：发布前对 mini-pi 仓库做一次检查……
  完整指令：/path/to/06-skills/examples/release-check/SKILL.md
</skills>
任务与某个 Skill 相符时，先用 read 读取它的「完整指令」并按其中步骤执行；……
```

只列允许自取的 Skill；每项带**完整指令的路径**——那是模型接下来要 read 的目标。清单为空时返回空串，不往系统提示里塞空标签。

### 系统提示的注入点：`system_suffix`

清单怎么进入请求？第 5 章的 `Agent` 加了一个参数：

```python
agent = Agent(provider, session, api, system_suffix=prompt_block)

# agent.py 的改动只有两行：
prompt = SYSTEM_PROMPT + ("\n\n" + self.system_suffix if self.system_suffix else "")
return [{"role": "system", "content": prompt}] + self.session.build_context()
```

这是本章对第 5 章唯一的修改，也是它想讲的设计观点：**系统提示本来就是宿主组装的产物**（提示词、工具清单、Skill 清单都是"每次请求要带上的状态"），给它一条显式的追加通道，比让每个功能各自改写提示更诚实。

`/skill` 命令注册（`register_skill_command`）复用第 5 章的命令注册表；处理器把 `skill.full_text()` 与用户参数拼成一条 user 消息交给 `agent.prompt()`。强制加载对 `disable-model-invocation` 的 Skill 同样有效——这个标志的全部含义就是"不进自动清单，但保留手动入口"。

## 文件结构

```
06-skills/
├── src/
│   ├── bootstrap.py     # 把第 5 章 src 加入 sys.path
│   ├── skills.py        # 发现、校验、清单、命令（核心 ~150 行）
│   ├── main.py          # 演示入口（离线剧本 / 真实模型）
│   └── test_skills.py   # 15 个离线测试
├── examples/
│   ├── tutorial-contrib/SKILL.md          # 纯指令型：贡献检查单
│   └── release-check/
│       ├── SKILL.md                       # 捆绑脚本型
│       └── scripts/check.sh
├── requirements.txt
└── README.md
```

本章复用第 2～5 章的实现（Provider、文件工具、会话、扩展宿主），需保留完整仓库目录。

## 如何运行

```bash
# 离线剧本：观察清单注入 → read 全文 → 按指令作答
python3 06-skills/src/main.py --demo "帮我对仓库做一次发布检查"

# 真实模型：说一个与 description 匹配的任务，看模型自己加载
export OPENAI_API_KEY="sk-..."
python3 06-skills/src/main.py "按 tutorial-contrib 的检查单准备好了再改第 4 章"

# 强制加载（不依赖模型的路由判断）
python3 06-skills/src/main.py    # 进入交互后输入：/skill release-check

# 追加自己的 Skill 目录
python3 06-skills/src/main.py --skills-dir ~/my-skills "……"
```

入口启动时会把 `<skills>` 清单原样打印到 stderr——第一次运行时值得看一眼：这几十行就是 Skill 占用的全部常驻上下文。

## 测试（无需 API key）

```bash
python3 -m pytest 06-skills/src/test_skills.py -v
```

覆盖 frontmatter 解析（含损坏与无 frontmatter）、递归发现、非法命名/超长描述/缺字段跳过、重名保留先发现者、未知字段容忍、清单只列自动 Skill、以及两条端到端路径：模型经 `read` 工具真实读取 SKILL.md，和 `/skill` 强制注入——断言系统提示带 `<skills>`、工具结果含全文、user 消息含请求。

## 练习

### 练习 1：祖先目录发现

pi 兼容 `.agents/skills/` 规范位置：从当前目录**向上**逐级查找 `.agents/skills` 目录，存在仓库根（`.git`）时停止。给 `load_skills` 加一个 `from_ancestors(cwd)` 辅助函数，返回所有命中目录。注意DirectoryName 去重，同一目录只扫一次。

### 练习 2：`/skills` 清单命令

注册一个 `/skills` 命令，列出当前已加载 Skill 的名字、描述与路径，标注哪些是"仅手动"。参考 pi 的做法：清单信息与启动诊断一致，用户不必翻 stderr。

### 练习 3：Skill 热刷新

实现 `/rescan` 命令：重新扫描目录、重建清单并更新 `agent.system_suffix`。对照 pi 的 `/reload`（改完 Skill 不用重启会话）；注意刷新后旧 Skill 对象可能仍被此前的 `/skill` 闭包引用——想清楚"清单位置"和"命令查找表"两个生命周期是否应该一致。

## 下一步

Skill 系统的核心是"知识按需装载"：不写一行新的执行逻辑，靠发现、清单和一条命令完成任务。第 7 章将复用第 5、6 章的全部模块组装成完整的 CLI——扩展通过 `-e` 加载，Skill 自动扫描 `.mini-pi/skills/` 与 `~/.mini-pi/skills/`，把教程带向可日常使用的形态。
