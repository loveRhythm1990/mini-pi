# mini-pi：从零构建编程智能体

一个中文教程，通过用 **Python** 实现一个简化版的 [pi](https://github.com/earendil-works/pi) 编程智能体，深入理解 coding agent 的核心原理。

> 注：pi 本身是用 TypeScript 编写的，但本教程用 Python 实现相同的概念，更适合 Python 开发者学习。

## 课程概览

本教程分为 6 个章节，每个章节都是独立可运行的 Python 代码，逐步构建一个完整的编程智能体框架：

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
- 上下文压缩：阈值 / 溢出 / 手动 `/compact` 三种触发
- 切点选择（不拆开 tool 调用与结果）、split turn、增量更新摘要、分支摘要
- CJK 感知的 token 估算（chars/4 会低估中文）

**代码量**：~700 行 Python

**对比 pi**：`session-manager.ts` 和 `compaction/`（参考提交 `7d75a0e`）

### 第 5 章：扩展机制（05-extensions/）
**核心概念**：事件钩子、动态工具注册、自定义命令

- 监听生命周期事件（turn_start、tool_call 等）
- 动态注册工具
- 拦截和改写工具调用
- 斜杠命令（slash commands）

**对比 pi**：`core/extensions/` 和 examples

### 第 6 章：CLI 与总结（06-cli/）
**核心概念**：终端 UI、架构对比、权衡取舍

- 简单的 TUI 实现
- 与 pi、Claude Code、OpenHands 的对比
- 设计哲学：为什么工具这么少？为什么不内置子 agent？

## 快速开始

### 环境要求
- Python 3.11+
- pip

### 安装依赖
```bash
pip install -r requirements.txt
```

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
```

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

5. **做练习**：每章末尾都有 2 个练习题

## 参考资料

- **pi 源码**：https://github.com/earendil-works/pi
- **参考提交**：`8bb180bec133199c913ca8ef9bb30bd9ab12a72b`（2026-10-12，第 1-3 章）；`7d75a0e`（第 4 章）
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
