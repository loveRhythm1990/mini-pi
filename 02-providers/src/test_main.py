#!/usr/bin/env python3
"""
第 2 章测试：消息格式转换和 Provider 接口
"""

from main import Message, ToolCall


def test_message_creation():
    """测试消息创建"""
    # 用户消息
    msg = Message(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"
    assert msg.tool_calls is None
    
    # 助手消息（带工具调用）
    msg = Message(
        role="assistant",
        content="让我执行命令",
        tool_calls=[
            ToolCall(id="call_1", name="bash", arguments={"command": "ls"})
        ]
    )
    assert msg.role == "assistant"
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].name == "bash"
    
    # 工具结果消息
    msg = Message(
        role="tool",
        content="main.py\nREADME.md",
        tool_call_id="call_1"
    )
    assert msg.role == "tool"
    assert msg.tool_call_id == "call_1"
    
    print("✅ 消息创建测试通过")


def test_tool_call():
    """测试工具调用数据结构"""
    tc = ToolCall(
        id="call_123",
        name="bash",
        arguments={"command": "echo hello"}
    )
    
    assert tc.id == "call_123"
    assert tc.name == "bash"
    assert tc.arguments["command"] == "echo hello"
    
    print("✅ 工具调用测试通过")


def test_openai_adapter():
    """测试 OpenAI 适配器的消息转换"""
    try:
        from openai_adapter import OpenAIAdapter
    except ImportError as e:
        print(f"⚠️  跳过 OpenAI 适配器测试（缺少依赖: {e}）")
        return
    
    # 模拟适配器（不需要真实 API key）
    class MockOpenAIAdapter(OpenAIAdapter):
        def __init__(self):
            self.model = "gpt-4o-mini"
        
        def create_completion(self, messages, tools, stream=False):
            pass
    
    adapter = MockOpenAIAdapter()
    
    # 测试：统一格式 -> OpenAI 格式
    messages = [
        Message(role="user", content="请执行命令"),
        Message(
            role="assistant",
            content="好的",
            tool_calls=[
                ToolCall(id="call_1", name="bash", arguments={"command": "ls"})
            ]
        ),
        Message(role="tool", content="main.py", tool_call_id="call_1")
    ]
    
    native = adapter.to_native_messages(messages)
    
    # 验证转换结果
    assert len(native) == 3
    
    # 第一条：用户消息
    assert native[0]["role"] == "user"
    assert native[0]["content"] == "请执行命令"
    
    # 第二条：助手消息（带工具调用）
    assert native[1]["role"] == "assistant"
    assert native[1]["content"] == "好的"
    assert len(native[1]["tool_calls"]) == 1
    assert native[1]["tool_calls"][0]["function"]["name"] == "bash"
    
    # 第三条：工具结果
    assert native[2]["role"] == "tool"
    assert native[2]["tool_call_id"] == "call_1"
    assert native[2]["content"] == "main.py"
    
    print("✅ OpenAI 适配器测试通过")


def test_anthropic_adapter():
    """测试 Anthropic 适配器的消息转换"""
    try:
        from anthropic_adapter import AnthropicAdapter
    except ImportError as e:
        print(f"⚠️  跳过 Anthropic 适配器测试（缺少依赖: {e}）")
        return
    
    # 模拟适配器
    class MockAnthropicAdapter(AnthropicAdapter):
        def __init__(self):
            self.model = "claude-3-5-sonnet-20241022"
        
        def create_completion(self, messages, tools, stream=False):
            pass
    
    adapter = MockAnthropicAdapter()
    
    # 测试：统一格式 -> Anthropic 格式
    messages = [
        Message(role="user", content="请执行命令"),
        Message(
            role="assistant",
            content="好的",
            tool_calls=[
                ToolCall(id="toolu_1", name="bash", arguments={"command": "ls"})
            ]
        ),
        Message(role="tool", content="main.py", tool_call_id="toolu_1")
    ]
    
    native = adapter.to_native_messages(messages)
    
    # 验证转换结果
    assert len(native) == 3
    
    # 第一条：用户消息
    assert native[0]["role"] == "user"
    assert native[0]["content"] == "请执行命令"
    
    # 第二条：助手消息（带工具调用）
    assert native[1]["role"] == "assistant"
    assert isinstance(native[1]["content"], list)
    
    # 文本块
    text_blocks = [b for b in native[1]["content"] if b["type"] == "text"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "好的"
    
    # 工具调用块
    tool_blocks = [b for b in native[1]["content"] if b["type"] == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0]["name"] == "bash"
    assert tool_blocks[0]["id"] == "toolu_1"
    
    # 第三条：工具结果（在 user 消息中）
    assert native[2]["role"] == "user"
    assert isinstance(native[2]["content"], list)
    assert native[2]["content"][0]["type"] == "tool_result"
    assert native[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert native[2]["content"][0]["content"] == "main.py"
    
    print("✅ Anthropic 适配器测试通过")


if __name__ == "__main__":
    print("运行第 2 章测试...\n")
    
    test_message_creation()
    test_tool_call()
    test_openai_adapter()
    test_anthropic_adapter()
    
    print("\n✅ 所有测试通过！")
