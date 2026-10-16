# mini-pi：从零构建编程智能体

一个中文教程，通过用 **Python** 实现一个简化版的 [pi](https://github.com/earendil-works/pi) 编程智能体，深入理解 coding agent 的核心原理。

> 注：pi 本身是用 TypeScript 编写的，但本教程用 Python 实现相同的概念，更适合 Python 开发者学习。

## 课程概览

本教程前 7 章均已实现，从最小循环逐步构建带会话、扩展、Skill 和 CLI 的编程智能体；第 8 章讨论 Harness 的设计取舍与验证。前 7 章有自己的运行入口；第 5～7 章复用前面章节，因此需要保留完整仓库目录：

### 第 1 章：Agent 主循环（01-loop/）
**核心概念**：消息数组、工具调用、循环终止条件

实现一个最基础的 agent 循环：
- 发送消息给 LLM（使用 OpenAI API）
- 解析返回的工具调用（`tool_calls` 格式）
- 执行 bash 工具
- 将结果追加到消息列表（`role="tool"` 消息）
- 循环直到没有工具调用

**代码量**：~160 行 Python  
**模型**：OpenAI（第 2 章添加 Claude 支持）

### 第 2 章：多模型适配层（02-providers/）✅
**核心概念**：统一消息格式、流式输出、多厂商兼容

- 定义统一的 Message / ToolCall 数据结构
- 实现 OpenAI 和 Anthropic (Claude) 适配器
  - OpenAI：`tool_calls` + `role="tool"` 消息
  - Claude：`tool_use` / `tool_result` 内容块
- 支持流式响应
- 会话中途切换模型

**代码量**：~400 行 Python

**对比 pi**：`packages/ai/` 的设计（TypeScript）

### 第 3 章：文件工具（03-tools/）✅
**核心概念**：工具定义、参数验证、错误处理

- read：读取文件内容
- write：写入文件
- edit：精确的文本替换（exact unique match）
- 工具输出截断
- 并发工具调用的互斥

**代码量**：~300 行 Python

**对比 pi**：`packages/coding-agent/src/core/tools/`

### 第 4 章：会话管理与上下文压缩（04-sessions/）✅
**核心概念**：持久化、恢复、分支、压缩

- JSONL 格式存储（header + 只追加的 entry）
- 基于 id/parentId 的树形结构，leaf 指针与分支
- 恢复会话、从 entry 投影出 LLM 上下文
- 消息级持久化与事故恢复：turn 非原子单位，aborted 半截消息、API 错误留痕，悬空调用补结果而不重放（第 5 章实现补齐）
- 上下文压缩：阈值 / 溢出 / 手动 `/compact` 三种触发
- 切点选择（不拆开 tool 调用与结果）、split turn、增量更新摘要、分支摘要
- CJK 感知的 token 估算（chars/4 会低估中文）

**代码量**：~700 行 Python

**对比 pi**：`session-manager.ts` 和 `compaction/`（参考提交 `7d75a0e`）

### 第 5 章：扩展机制（[05-extensions/](05-extensions/README.md)）✅
**核心概念**：事件钩子、动态工具注册、自定义命令

- 接回 Provider、文件工具与会话压缩，统一执行路径
- 动态工具注册与 JSON Schema 参数验证
- 生命周期钩子、调用拦截、参数和结果改写、压缩取消
- 注册斜杠命令，按文件加载扩展
- 扩展的安装与配置：pi 的目录发现、`--extension`、信任确认；生产者-消费者模型与 steering / follow-up 队列
- 日志、bash 拦截、项目查看三个示例扩展
- 中断后补齐工具结果，恢复时不自动重放

**对比 pi**：`core/extensions/` 和 examples

### 第 6 章：Skill 系统（[06-skills/](06-skills/README.md)）✅
**核心概念**：渐进式披露、描述即路由、按需装载的指令

- 行为（扩展）与知识（Skill）的分界；需求→机制版图
- SKILL.md 规范形态：frontmatter 校验、重名先到先得、捆绑脚本目录
- 启动只挂「名字+描述」清单，全文由模型用 read 工具自取
- `/skill <名字> [请求]` 强制加载；`disable-model-invocation` 三态控制
- Agent 增加 `system_suffix` 注入点：系统提示也是宿主组装的产物

**代码量**：~150 行 Python（不含入口与测试）

**对比 pi**：`docs/skills.md` 与 Agent Skills 规范（agentskills.io，与 Claude Code 通用）

### 第 7 章：CLI 与总结（[07-cli/](07-cli/README.md)）✅
**核心概念**：终端 UI、架构对比、权衡取舍

- 交互终端、输入历史与命令补全
- 一次性任务、stdin、流式输出、stdout/stderr 分离
- 按项目恢复会话，处理错误、中断和退出码
- 无需 API key 的离线演示与可执行启动器
- 与 pi、Claude Code、OpenHands 公开架构对比
- 设计取舍：工具数量、扩展边界与子 Agent

### 第 8 章：Harness——设计思路、取舍与验证（[08-harness/](08-harness/README.md)）
**核心概念**：上下文与行动、代码理解、可靠性与评估

- 区分 Prompt、上下文、Harness 与工具执行
- 理解 Agent 搜索、阅读、修改和验证代码的过程
- 分析极简设计、计划、子 Agent、索引与权限的取舍
- 区分作者观点、实验事实与本教程的设计判断
- 用受控实验和真实任务判断新增机制是否有效

本章是理论总结，没有新增运行入口。

## 快速开始

### 环境要求
- Python 3.11+
- pip

### 安装依赖
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 先运行最终版本（无需 API key）

```bash
python3 07-cli/src/main.py --demo "介绍这个项目"
python3 07-cli/src/main.py --demo --interactive
```

离线模式使用确定性的假模型，只演示调用流程，不理解任意任务或生成真实摘要。

### 设置 API Key
```bash
export OPENAI_API_KEY="your-api-key"

# 可选：使用自定义 API 端点（如 Azure OpenAI）
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 可选：指定模型（默认 gpt-4o-mini）
export OPENAI_MODEL="gpt-4o"
```

### 运行第 1 章
```bash
python3 01-loop/src/main.py
```

### 运行第 2 章（多模型适配层）
```bash
# 设置 OpenAI API key（主要）
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o-mini"  # 可选

# 或设置 Anthropic API key（可选）
export ANTHROPIC_API_KEY="sk-ant-..."

python3 02-providers/src/main.py
```

### 运行第 3 章（文件工具）
```bash
export OPENAI_API_KEY="sk-..."
python3 03-tools/src/main.py
```

### 运行第 4 章（会话管理与压缩）
```bash
export OPENAI_API_KEY="sk-..."
python3 04-sessions/src/main.py        # 新会话
python3 04-sessions/src/main.py -c     # 恢复最近的会话
```

### 运行测试（无需 API key）
```bash
# 运行全部七章（独立进程，避免各章的同名 main.py 冲突）
python3 scripts/test.py

# 第 1 章测试
pytest 01-loop/src/test_main.py -v
# 或
python3 01-loop/src/test_main.py

# 第 2 章测试
pytest 02-providers/src/test_main.py -v

# 第 3 章测试
pytest 03-tools/src/test_tools.py -v

# 第 4 章测试
pytest 04-sessions/src/test_sessions.py -v

# 第 5 章测试
pytest 05-extensions/src/test_extensions.py -v

# 第 6 章测试
pytest 06-skills/src/test_skills.py -v

# 第 7 章测试
pytest 07-cli/src/test_cli.py -v
```

### 运行第 5～7 章

```bash
# 扩展机制演示
python3 05-extensions/src/main.py --demo "介绍这个项目"

# Skill 系统演示（离线剧本：清单注入 → read 全文 → 按指令作答）
python3 06-skills/src/main.py --demo "帮我对仓库做一次发布检查"

# 连接真实模型，执行一次任务
python3 07-cli/src/main.py "读取 README.md，说明每章实现了什么"

# 恢复当前项目最近的会话
python3 07-cli/src/main.py -c

# 查看完整参数
python3 07-cli/src/main.py --help
```

模型配置、额外扩展、Skill 目录和管道输出见[第 7 章](07-cli/README.md)。工具直接在当前机器执行，本教程未实现沙箱和完整权限系统。

## 学习路径

1. **先玩一玩 pi**：理解 coding agent 能做什么
   ```bash
   git clone https://github.com/earendil-works/pi.git
   cd pi && npm install && npm run build
   ./packages/coding-agent/bin/pi.js
   ```
   （注：pi 是 TypeScript/Node.js 项目）

2. **阅读每章的 README.md**：理解核心概念

3. **运行并修改代码**：动手实践（Python 版本）

4. **对比 pi 源码**：看看生产级实现的细节（附 TypeScript 语法解释）

5. **做练习**：每章末尾都有练习题

## 参考资料

- **pi 源码**：https://github.com/earendil-works/pi
- **参考提交**：`8bb180bec133199c913ca8ef9bb30bd9ab12a72b`（2026-10-12，第 1-3、5-6 章）；`7d75a0e`（第 4 章）
- **作者博客**：https://mariozechner.at
- **OpenAI API 文档**：https://platform.openai.com/docs/guides/function-calling
- **Claude API 文档**：https://docs.anthropic.com/en/docs/tool-use

## 设计哲学

mini-pi 遵循以下原则：

- **极简**：每个章节都是能独立运行的最小实现
- **可读**：代码和注释都用中文，优先清晰而非性能
- **渐进**：从 150 行 Python 开始，逐步增加复杂度
- **实用**：每个特性都对应真实需求，不做过度设计
- **语言友好**：用 Python 实现，但保留与 pi（TypeScript）的对比

## 贡献

欢迎提 issue 和 PR！

## 许可证

MIT License
