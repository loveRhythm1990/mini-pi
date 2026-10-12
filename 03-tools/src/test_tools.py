#!/usr/bin/env python3
"""
第 3 章测试：文件工具
"""

import os
import tempfile
import threading
from tools import tool_read, tool_write, tool_edit, execute_tool


def test_read():
    """测试 read 工具"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Hello, World!")
        temp_path = f.name
    
    try:
        # 读取文件
        result = tool_read(temp_path)
        assert "Hello, World!" in result
        assert "错误" not in result
        
        # 读取不存在的文件
        result = tool_read("nonexistent.txt")
        assert "错误" in result
        assert "不存在" in result
        
        print("✅ read 工具测试通过")
        
    finally:
        os.remove(temp_path)


def test_write():
    """测试 write 工具"""
    temp_path = tempfile.mktemp(suffix='.txt')
    
    try:
        # 写入文件
        result = tool_write(temp_path, "Hello, mini-pi!")
        assert "✅" in result
        assert temp_path in result
        
        # 验证文件内容
        with open(temp_path, 'r') as f:
            content = f.read()
        assert content == "Hello, mini-pi!"
        
        # 覆盖文件
        result = tool_write(temp_path, "New content")
        assert "✅" in result
        
        with open(temp_path, 'r') as f:
            content = f.read()
        assert content == "New content"
        
        print("✅ write 工具测试通过")
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_edit_success():
    """测试 edit 工具（成功情况）"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Hello, World!\nThis is a test.\n")
        temp_path = f.name
    
    try:
        # 精确替换
        result = tool_edit(temp_path, "World", "mini-pi")
        assert "✅" in result
        
        # 验证结果
        with open(temp_path, 'r') as f:
            content = f.read()
        assert "Hello, mini-pi!" in content
        assert "World" not in content
        
        print("✅ edit 工具成功测试通过")
        
    finally:
        os.remove(temp_path)


def test_edit_not_found():
    """测试 edit 工具（未找到）"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Hello, World!")
        temp_path = f.name
    
    try:
        # 尝试替换不存在的文本
        result = tool_edit(temp_path, "nonexistent", "something")
        assert "错误" in result
        assert "未找到" in result
        
        print("✅ edit 工具未找到测试通过")
        
    finally:
        os.remove(temp_path)


def test_edit_multiple_matches():
    """测试 edit 工具（多处匹配）"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("foo bar foo baz")
        temp_path = f.name
    
    try:
        # 尝试替换出现多次的文本
        result = tool_edit(temp_path, "foo", "xyz")
        assert "错误" in result
        assert "处匹配" in result or "不唯一" in result
        
        # 文件内容不应改变
        with open(temp_path, 'r') as f:
            content = f.read()
        assert content == "foo bar foo baz"
        
        print("✅ edit 工具多处匹配测试通过")
        
    finally:
        os.remove(temp_path)


def test_concurrent_edit():
    """测试并发编辑的互斥控制"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("line1\nline2\nline3\n")
        temp_path = f.name
    
    try:
        results = []
        
        def edit_task(old, new):
            result = tool_edit(temp_path, old, new)
            results.append((old, new, result))
        
        # 启动多个线程同时编辑
        threads = [
            threading.Thread(target=edit_task, args=("line1", "LINE1")),
            threading.Thread(target=edit_task, args=("line2", "LINE2")),
            threading.Thread(target=edit_task, args=("line3", "LINE3")),
        ]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证所有编辑都成功了
        with open(temp_path, 'r') as f:
            content = f.read()
        
        assert "LINE1" in content
        assert "LINE2" in content
        assert "LINE3" in content
        
        print("✅ 并发编辑测试通过")
        
    finally:
        os.remove(temp_path)


def test_execute_tool():
    """测试 execute_tool 函数"""
    temp_path = tempfile.mktemp(suffix='.txt')
    
    try:
        # 测试通过 execute_tool 调用
        result = execute_tool("write", {"path": temp_path, "content": "test"})
        assert "✅" in result
        
        result = execute_tool("read", {"path": temp_path})
        assert "test" in result
        
        result = execute_tool("edit", {
            "path": temp_path,
            "old_str": "test",
            "new_str": "TEST"
        })
        assert "✅" in result
        
        # 未知工具
        result = execute_tool("unknown", {})
        assert "错误" in result
        assert "未知工具" in result
        
        print("✅ execute_tool 测试通过")
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    print("运行第 3 章测试...\n")
    
    test_read()
    test_write()
    test_edit_success()
    test_edit_not_found()
    test_edit_multiple_matches()
    test_concurrent_edit()
    test_execute_tool()
    
    print("\n✅ 所有测试通过！")
