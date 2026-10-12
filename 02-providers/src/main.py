#!/usr/bin/env python3
"""
第 2 章：多模型适配层

核心概念：
1. 定义统一的 Message / ToolCall 数据结构
2. 实现 Provider 抽象接口
3. 为 OpenAI 和 Anthropic 编写适配器
4. 支持流式输出
"""

import os
import sys
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Iterator, Any


# ============ 统一消息格式 ============

@dataclass
class ToolCall:
    """统一的工具调用格式"""
    id: str
    name: str
    arguments: dict  # 统一用字典，不是 JSON 字符串


@dataclass
class Message:
    """统一的消息格式"""
    role: str  # "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # 当 role == "tool" 时使用
    
    def __repr__(self):
        parts = [f"Message(role={self.role!r}"]
        if self.content:
            preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
            parts.append(f"content={preview!r}")
        if self.tool_calls:
            parts.append(f"tool_calls={len(self.tool_calls)} calls")
        if self.tool_call_id:
            parts.append(f"tool_call_id={self.tool_call_id!r}")
        return ", ".join(parts) + ")"


# ============ Provider 抽象接口 ============

class Provider(ABC):
    """多模型适配层的抽象接口"""
    
    @abstractmethod
    def create_completion(
        self,
        messages: list[Message],
        tools: list[dict],
        stream: bool = False
    ) -> Message | Iterator[dict]:
        """
        发送请求，返回 Message 或流式事件
        
        Args:
            messages: 统一格式的消息历史
            tools: 工具定义（JSON Schema 格式）
            stream: 是否流式输出
            
        Returns:
            如果 stream=False，返回完整的 Message
            如果 stream=True，返回事件迭代器
        """
        pass
    
    @abstractmethod
    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        """将统一格式转换为厂商原生格式"""
        pass
    
    @abstractmethod
    def from_native_message(self, native_msg: Any) -> Message:
        """将厂商原生格式转换为统一格式"""
        pass


# ============ Bash 工具（同第1章） ============

import subprocess

BASH_TOOL = {
    "name": "bash",
    "description": "执行 bash 命令。可以用来查看文件、运行程序、操作文件系统等。",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 bash 命令"
            }
        },
        "required": ["command"]
    }
}


def execute_bash(command: str) -> tuple[str, bool]:
    """执行 bash 工具"""
    print(f"\n🔧 执行命令: {command}")
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        output = result.stdout + result.stderr
        
        if len(output) > 10000:
            output = output[:10000] + "\n...(输出被截断)"
        
        print(f"✅ 命令完成，输出长度: {len(output)} 字节")
        return output, result.returncode != 0
        
    except subprocess.TimeoutExpired:
        print("❌ 命令超时")
        return "错误: 命令执行超时（30秒）", True
    except Exception as e:
        print(f"❌ 命令失败: {e}")
        return f"错误: {str(e)}", True


def execute_tool_call(tool_call: ToolCall) -> Message:
    """执行工具调用，返回统一格式的工具结果消息"""
    if tool_call.name == "bash":
        command = tool_call.arguments["command"]
        output, is_error = execute_bash(command)
        
        return Message(
            role="tool",
            content=output,
            tool_call_id=tool_call.id
        )
    
    return Message(
        role="tool",
        content=f"错误: 未知工具 '{tool_call.name}'",
        tool_call_id=tool_call.id
    )


# ============ Agent 主循环（使用 Provider）============

def agent_loop(provider: Provider, messages: list[Message], max_steps: int = 10, stream: bool = False) -> None:
    """
    Agent 主循环：使用 Provider 接口，支持任意模型
    
    Args:
        provider: 模型提供者（OpenAI / Anthropic 等）
        messages: 统一格式的消息历史
        max_steps: 最大循环步数
        stream: 是否流式输出
    """
    
    for step in range(max_steps):
        print(f"\n{'=' * 60}")
        print(f"第 {step + 1} 步 / 最多 {max_steps} 步")
        print('=' * 60)
        
        print("\n🤖 正在调用模型...")
        
        if stream:
            # 流式模式
            response_stream = provider.create_completion(messages, [BASH_TOOL], stream=True)
            
            print("\n💬 模型回复（流式）:")
            
            final_message = None
            for event in response_stream:
                if event["type"] == "text_delta":
                    print(event["text"], end='', flush=True)
                elif event["type"] == "tool_call_delta":
                    pass  # 工具调用的增量，暂时不显示
                elif event["type"] == "done":
                    final_message = event["message"]
                    print()  # 换行
            
            if not final_message:
                print("❌ 流式输出异常：没有收到完整消息")
                break
            
            assistant_message = final_message
        else:
            # 非流式模式
            assistant_message = provider.create_completion(messages, [BASH_TOOL], stream=False)
            
            if assistant_message.content:
                print(f"\n💬 模型说: {assistant_message.content}")
        
        messages.append(assistant_message)
        
        # 检查是否有工具调用
        if not assistant_message.tool_calls:
            print("\n✨ 没有更多工具调用，任务完成！")
            break
        
        # 执行工具调用
        print(f"\n🔨 执行 {len(assistant_message.tool_calls)} 个工具调用...")
        
        for tool_call in assistant_message.tool_calls:
            tool_result = execute_tool_call(tool_call)
            messages.append(tool_result)
    
    if step == max_steps - 1:
        print("\n⚠️  达到最大步数限制，强制停止")


# ============ 主程序 ============

def main():
    # 确定使用哪个 provider
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    
    if not openai_key and not anthropic_key:
        print("错误: 请至少设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY", file=sys.stderr)
        print("\n示例:")
        print("  export OPENAI_API_KEY='sk-...'")
        print("  export OPENAI_BASE_URL='https://api.openai.com/v1'  # 可选")
        print("  export OPENAI_MODEL='gpt-4o-mini'  # 可选")
        print("\n或:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)
    
    print("🚀 mini-pi Agent 启动（第 2 章：多模型适配层）\n")
    
    # 演示 1: OpenAI（非流式）
    if openai_key:
        from openai_adapter import OpenAIAdapter
        
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.environ.get("OPENAI_BASE_URL")
        
        print(f"📘 使用 OpenAI 模型: {model}")
        if base_url:
            print(f"🔗 API 地址: {base_url}")
        
        provider = OpenAIAdapter(api_key=openai_key, model=model, base_url=base_url)
        
        messages = [
            Message(role="user", content="请用 ls 命令查看当前目录的内容")
        ]
        
        print("\n" + "=" * 60)
        print("演示 1: OpenAI 非流式模式")
        print("=" * 60)
        
        agent_loop(provider, messages.copy(), max_steps=3, stream=False)
        
        print("\n" + "=" * 60)
        print("演示 2: OpenAI 流式模式")
        print("=" * 60)
        
        messages = [
            Message(role="user", content="请用 echo 命令输出 'Hello from OpenAI'")
        ]
        
        agent_loop(provider, messages.copy(), max_steps=3, stream=True)
    
    # 演示 3: Anthropic（如果设置了 key）
    if anthropic_key:
        from anthropic_adapter import AnthropicAdapter
        
        print("\n" + "=" * 60)
        print("演示 3: 切换到 Anthropic Claude")
        print("=" * 60)
        
        provider = AnthropicAdapter(api_key=anthropic_key)
        
        messages = [
            Message(role="user", content="请用 echo 命令输出 'Hello from Claude'")
        ]
        
        agent_loop(provider, messages.copy(), max_steps=3, stream=False)
    
    print("\n✅ 所有演示完成！")


if __name__ == "__main__":
    main()
