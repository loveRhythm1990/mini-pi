"""
文件工具实现：read / write / edit / bash
"""

import os
import sys
import signal
import subprocess
import threading
import time
from typing import Iterator


# ============ 文件锁（并发控制） ============

_file_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """获取文件的互斥锁"""
    abs_path = os.path.abspath(path)
    
    with _locks_lock:
        if abs_path not in _file_locks:
            _file_locks[abs_path] = threading.Lock()
        return _file_locks[abs_path]


# ============ 工具实现 ============

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
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return f"✅ 已写入文件: {path} ({len(content)} 字符)"
        
    except PermissionError:
        return f"错误: 没有权限写入文件: {path}"
    except Exception as e:
        return f"错误: {str(e)}"


def _truncate_tail(content: str, max_lines: int = 2000, max_bytes: int = 50 * 1024) -> tuple[str, bool, str | None]:
    """
    从尾部截断内容（保留最后N行/字节）
    模拟 pi 的 truncateTail 行为：packages/coding-agent/src/core/tools/truncate.ts
    
    Args:
        content: 要截断的内容
        max_lines: 最大行数（默认2000）
        max_bytes: 最大字节数（默认50KB）
        
    Returns:
        (truncated_content, was_truncated, full_content_for_temp_file)
    """
    total_bytes = len(content.encode('utf-8'))
    lines = content.split('\n')
    total_lines = len(lines)
    
    # 如果不需要截断
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return content, False, None
    
    # 从后向前累积行
    output_lines = []
    output_bytes = 0
    
    for i in range(len(lines) - 1, -1, -1):
        if len(output_lines) >= max_lines:
            break
        
        line = lines[i]
        line_bytes = len(line.encode('utf-8')) + (1 if output_lines else 0)  # +1 for newline
        
        if output_bytes + line_bytes > max_bytes:
            break
        
        output_lines.insert(0, line)
        output_bytes += line_bytes
    
    truncated_content = '\n'.join(output_lines)
    return truncated_content, True, content


def tool_bash(command: str, timeout: int = 30, cwd: str | None = None) -> str:
    """
    执行 bash 命令（基于 pi 的实现）
    
    特性：
    - 超时控制（默认30秒）
    - 尾部截断（保留最后2000行或50KB，先达到哪个限制就截断）
    - 合并 stdout 和 stderr
    - 报告退出码
    - 支持自定义工作目录
    - 进程树终止（超时或中断时）
    - 截断时保存完整输出到临时文件
    
    Args:
        command: 要执行的 bash 命令
        timeout: 超时时间（秒），默认30秒
        cwd: 工作目录，默认为当前目录
        
    Returns:
        命令输出（包含退出码信息和临时文件路径）
    """
    try:
        # pi 的实现会合并 stdout 和 stderr
        # 参考：packages/coding-agent/src/core/tools/bash.ts
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并到 stdout
            text=True,
            cwd=cwd,
            # 创建新进程组，方便后续终止整个进程树
            start_new_session=sys.platform != 'win32'
        )
        
        try:
            # 使用 communicate 并带超时（pi 的做法）
            output, _ = process.communicate(timeout=timeout)
            exit_code = process.returncode
            
            # pi 的输出截断策略：只保留尾部（2000行或50KB）
            # 参考：packages/coding-agent/src/core/tools/truncate.ts
            truncated_output, was_truncated, full_output = _truncate_tail(output)
            
            # 如果被截断，保存完整输出到临时文件
            temp_file_path = None
            if was_truncated and full_output:
                import tempfile
                fd, temp_file_path = tempfile.mkstemp(suffix='.txt', prefix='bash_output_')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(full_output)
                except:
                    temp_file_path = None
            
            # 构建结果
            result = truncated_output if truncated_output else "(命令成功执行，无输出)"
            
            # 添加截断提示
            if was_truncated:
                total_lines = len(output.split('\n'))
                total_bytes = len(output.encode('utf-8'))
                result += f"\n\n[输出已截断：总共 {total_lines} 行 / {total_bytes} 字节，仅显示尾部]"
                if temp_file_path:
                    result += f"\n[完整输出已保存到: {temp_file_path}]"
            
            # 报告退出码（pi 会在输出中标注）
            if exit_code != 0:
                result += f"\n\n❌ 退出码: {exit_code}"
            
            return result
                
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as interrupted:
            # 超时，终止进程树（pi 的做法）
            try:
                if sys.platform != 'win32':
                    os.killpg(process.pid, signal.SIGTERM)
                    time.sleep(0.1)
                    # 如果还没终止，强制 kill
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        # macOS 对已经消失的进程组也可能返回 EPERM。
                        if process.poll() is None:
                            raise
                else:
                    process.terminate()
                    time.sleep(0.1)
                    try:
                        process.kill()
                    except:
                        pass
            except ProcessLookupError:
                pass
            
            # 尝试获取已有的输出
            try:
                output, _ = process.communicate(timeout=0.5)
            except:
                output = ""

            if isinstance(interrupted, KeyboardInterrupt):
                raise  # 清理子进程后交给 CLI 处理用户中断。
            
            return (
                output + 
                f"\n\n❌ 命令超时（{timeout}秒）已终止"
            )
        
    except FileNotFoundError:
        return f"错误: 未找到 shell 或命令不存在"
    except Exception as e:
        return f"错误: {str(e)}"


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
    with _get_file_lock(path):
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


# ============ 工具定义 ============

TOOLS = [
    {
        "name": "bash",
        "description": (
            "执行 bash 命令。可以运行任何 shell 命令（ls, grep, git 等）。\n"
            "注意：\n"
            "- 命令会在 30 秒后超时\n"
            "- stdout 和 stderr 会合并输出\n"
            "- 过长的输出会自动截断（保留头部和尾部）\n"
            "- 会报告命令的退出码\n"
            "警告：bash 可以执行任何系统命令，请谨慎使用！"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 bash 命令"
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（可选，默认为当前目录）"
                }
            },
            "required": ["command"]
        }
    },
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


def execute_tool(name: str, arguments: dict) -> str:
    """
    执行工具调用
    
    Args:
        name: 工具名称
        arguments: 工具参数
        
    Returns:
        工具执行结果
    """
    if name == "bash":
        return tool_bash(arguments["command"], cwd=arguments.get("cwd"))
    elif name == "read":
        return tool_read(arguments["path"])
    elif name == "write":
        return tool_write(arguments["path"], arguments["content"])
    elif name == "edit":
        return tool_edit(arguments["path"], arguments["old_str"], arguments["new_str"])
    else:
        return f"错误: 未知工具 '{name}'"
