"""
第 1 章测试：模拟 OpenAI 响应，验证循环逻辑
"""

import pytest
from typing import List, Dict, Any

# 模拟 OpenAI 的响应结构
class MockToolCall:
    def __init__(self, id: str, function_name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = type('obj', (object,), {
            'name': function_name,
            'arguments': arguments
        })()

class MockMessage:
    def __init__(self, content: str = None, tool_calls: List[MockToolCall] = None):
        self.content = content
        self.tool_calls = tool_calls

class MockChoice:
    def __init__(self, message: MockMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason

class MockChatCompletion:
    def __init__(self, choices: List[MockChoice]):
        self.choices = choices

# 模拟的 agent 循环（简化版）
def mock_agent_loop(user_prompt: str, mock_responses: List[MockChatCompletion]) -> int:
    """
    模拟 agent 循环
    
    Returns:
        执行的步数
    """
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": user_prompt}
    ]
    step_count = 0
    max_steps = 10
    
    for step in range(min(max_steps, len(mock_responses))):
        step_count += 1
        response = mock_responses[step]
        message = response.choices[0].message
        
        # 添加 assistant 消息
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": message.tool_calls
        })
        
        # 检查工具调用
        if not message.tool_calls:
            # 没有工具调用，结束
            break
        
        # 模拟执行工具并添加结果
        for tool_call in message.tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": "模拟输出"
            })
    
    return step_count

# ============ 测试用例 ============

def test_single_step_tool_call():
    """测试：单步工具调用后终止"""
    mock_responses = [
        MockChatCompletion([
            MockChoice(MockMessage(
                content="我来查看一下文件",
                tool_calls=[
                    MockToolCall("call_1", "bash", '{"command": "ls"}')
                ]
            ))
        ]),
        MockChatCompletion([
            MockChoice(MockMessage(
                content="看到了 3 个文件：file1.txt, file2.py, README.md"
            ))
        ])
    ]
    
    steps = mock_agent_loop("列出文件", mock_responses)
    assert steps == 2, "应该执行 2 步：第 1 步调用工具，第 2 步返回结果"

def test_multi_step_tool_calls():
    """测试：多步工具调用"""
    mock_responses = [
        MockChatCompletion([
            MockChoice(MockMessage(
                tool_calls=[MockToolCall("call_1", "bash", '{"command": "pwd"}')]
            ))
        ]),
        MockChatCompletion([
            MockChoice(MockMessage(
                tool_calls=[MockToolCall("call_2", "bash", '{"command": "ls"}')]
            ))
        ]),
        MockChatCompletion([
            MockChoice(MockMessage(content="完成了"))
        ])
    ]
    
    steps = mock_agent_loop("查看目录", mock_responses)
    assert steps == 3, "应该执行 3 步"

def test_immediate_termination():
    """测试：立即终止（无工具调用）"""
    mock_responses = [
        MockChatCompletion([
            MockChoice(MockMessage(content="好的，我明白了"))
        ])
    ]
    
    steps = mock_agent_loop("你好", mock_responses)
    assert steps == 1, "应该只执行 1 步就终止"

def test_multiple_tool_calls_in_one_turn():
    """测试：单轮多个工具调用"""
    mock_responses = [
        MockChatCompletion([
            MockChoice(MockMessage(
                content="让我同时检查两个东西",
                tool_calls=[
                    MockToolCall("call_1", "bash", '{"command": "pwd"}'),
                    MockToolCall("call_2", "bash", '{"command": "ls"}')
                ]
            ))
        ]),
        MockChatCompletion([
            MockChoice(MockMessage(content="完成"))
        ])
    ]
    
    steps = mock_agent_loop("检查目录", mock_responses)
    assert steps == 2, "应该执行 2 步"

if __name__ == "__main__":
    # 可以直接运行此文件进行测试
    print("运行测试...")
    
    test_single_step_tool_call()
    print("✅ 测试 1 通过：单步工具调用")
    
    test_multi_step_tool_calls()
    print("✅ 测试 2 通过：多步工具调用")
    
    test_immediate_termination()
    print("✅ 测试 3 通过：立即终止")
    
    test_multiple_tool_calls_in_one_turn()
    print("✅ 测试 4 通过：单轮多个工具调用")
    
    print("\n✅ 所有测试通过！Agent 循环逻辑正确。")
