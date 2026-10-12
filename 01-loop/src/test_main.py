"""
第 1 章测试：模拟 LLM 响应，验证循环逻辑
"""

import pytest
from typing import List, Dict, Any

# 模拟的工具调用执行器
def mock_execute_tool_call(tool_name: str, input_data: Dict[str, Any]) -> str:
    """模拟工具执行"""
    if tool_name == "bash":
        if input_data.get("command") == "ls":
            return "file1.txt\nfile2.py\nREADME.md"
        return f"模拟执行: {input_data.get('command')}"
    return "未知工具"

# 模拟的 ContentBlock 类
class MockContentBlock:
    def __init__(self, block_type: str, **kwargs):
        self.type = block_type
        for key, value in kwargs.items():
            setattr(self, key, value)

# 模拟的响应
class MockResponse:
    def __init__(self, content: List[MockContentBlock], stop_reason: str = "end_turn"):
        self.content = content
        self.stop_reason = stop_reason

# 模拟的 agent 循环（简化版）
def mock_agent_loop(user_prompt: str, mock_responses: List[MockResponse]) -> int:
    """
    模拟 agent 循环
    
    Returns:
        执行的步数
    """
    messages = [{"role": "user", "content": user_prompt}]
    step_count = 0
    max_steps = 10
    
    for step in range(min(max_steps, len(mock_responses))):
        step_count += 1
        response = mock_responses[step]
        
        # 添加 assistant 消息
        messages.append({
            "role": "assistant",
            "content": response.content
        })
        
        # 检查工具调用
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        
        if not tool_uses:
            # 没有工具调用，结束
            break
        
        # 执行工具并添加结果
        tool_results = []
        for tool_use in tool_uses:
            output = mock_execute_tool_call(tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": output
            })
        
        messages.append({
            "role": "user",
            "content": tool_results
        })
    
    return step_count

# ============ 测试用例 ============

def test_single_step_tool_call():
    """测试：单步工具调用后终止"""
    mock_responses = [
        MockResponse(
            content=[
                MockContentBlock("text", text="我来查看一下文件"),
                MockContentBlock("tool_use", id="call_1", name="bash", input={"command": "ls"})
            ]
        ),
        MockResponse(
            content=[
                MockContentBlock("text", text="看到了 3 个文件：file1.txt, file2.py, README.md")
            ]
        )
    ]
    
    steps = mock_agent_loop("列出文件", mock_responses)
    
    assert steps == 2, "应该执行 2 步：第 1 步调用工具，第 2 步返回结果"

def test_multi_step_tool_calls():
    """测试：多步工具调用"""
    mock_responses = [
        MockResponse(
            content=[
                MockContentBlock("tool_use", id="call_1", name="bash", input={"command": "pwd"})
            ]
        ),
        MockResponse(
            content=[
                MockContentBlock("tool_use", id="call_2", name="bash", input={"command": "ls"})
            ]
        ),
        MockResponse(
            content=[
                MockContentBlock("text", text="完成了")
            ]
        )
    ]
    
    steps = mock_agent_loop("查看目录", mock_responses)
    
    assert steps == 3, "应该执行 3 步"

def test_immediate_termination():
    """测试：立即终止（无工具调用）"""
    mock_responses = [
        MockResponse(
            content=[
                MockContentBlock("text", text="好的，我明白了")
            ]
        )
    ]
    
    steps = mock_agent_loop("你好", mock_responses)
    
    assert steps == 1, "应该只执行 1 步就终止"

def test_bash_tool_execution():
    """测试：bash 工具执行"""
    # 测试 ls 命令
    output = mock_execute_tool_call("bash", {"command": "ls"})
    assert "file1.txt" in output
    assert "README.md" in output
    
    # 测试其他命令
    output = mock_execute_tool_call("bash", {"command": "echo hello"})
    assert "模拟执行" in output

if __name__ == "__main__":
    # 可以直接运行此文件进行测试
    print("运行测试...")
    test_single_step_tool_call()
    print("✅ 测试 1 通过")
    
    test_multi_step_tool_calls()
    print("✅ 测试 2 通过")
    
    test_immediate_termination()
    print("✅ 测试 3 通过")
    
    test_bash_tool_execution()
    print("✅ 测试 4 通过")
    
    print("\n✅ 所有测试通过！Agent 循环逻辑正确。")
