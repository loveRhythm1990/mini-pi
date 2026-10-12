"""
文件工具实现：read / write / edit
"""

import os
import threading


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
    if name == "read":
        return tool_read(arguments["path"])
    elif name == "write":
        return tool_write(arguments["path"], arguments["content"])
    elif name == "edit":
        return tool_edit(arguments["path"], arguments["old_str"], arguments["new_str"])
    else:
        return f"错误: 未知工具 '{name}'"
