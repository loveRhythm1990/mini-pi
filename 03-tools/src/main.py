#!/usr/bin/env python3
"""
第 3 章：文件工具

核心概念：
1. read / write / edit 三个文件工具
2. edit 使用 exact unique match 原则
3. 工具输出截断（保护上下文窗口）
4. 并发工具调用的互斥控制
"""

import os
import sys

# 添加第 2 章的路径，以便导入 Provider
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../02-providers/src'))

from main import Message, ToolCall, Provider
from tools import TOOLS, execute_tool


# ============ Agent 主循环（使用文件工具） ============

def agent_loop(provider: Provider, messages: list[Message], max_steps: int = 10) -> None:
    """
    Agent 主循环：支持 read / write / edit 工具
    
    Args:
        provider: 模型提供者
        messages: 消息历史
        max_steps: 最大循环步数
    """
    
    for step in range(max_steps):
        print(f"\n{'=' * 60}")
        print(f"第 {step + 1} 步 / 最多 {max_steps} 步")
        print('=' * 60)
        
        print("\n🤖 正在调用模型...")
        
        # 调用模型
        assistant_message = provider.create_completion(messages, TOOLS, stream=False)
        
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
            print(f"\n🔧 调用工具: {tool_call.name}")
            print(f"   参数: {tool_call.arguments}")
            
            # 执行工具
            result = execute_tool(tool_call.name, tool_call.arguments)
            
            # 显示结果（截断长输出）
            result_preview = result[:200] + "..." if len(result) > 200 else result
            print(f"   结果: {result_preview}")
            
            # 追加工具结果到消息历史
            tool_result = Message(
                role="tool",
                content=result,
                tool_call_id=tool_call.id
            )
            messages.append(tool_result)
    
    if step == max_steps - 1:
        print("\n⚠️  达到最大步数限制，强制停止")


# ============ 主程序 ============

def main():
    # 检查 API key
    openai_key = os.environ.get("OPENAI_API_KEY")
    
    if not openai_key:
        print("错误: 请设置环境变量 OPENAI_API_KEY", file=sys.stderr)
        print("\n示例:")
        print("  export OPENAI_API_KEY='sk-...'")
        print("  export OPENAI_MODEL='gpt-4o-mini'  # 可选")
        sys.exit(1)
    
    from openai_adapter import OpenAIAdapter
    
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL")
    
    print("🚀 mini-pi Agent 启动（第 3 章：文件工具）\n")
    print(f"📘 使用模型: {model}")
    if base_url:
        print(f"🔗 API 地址: {base_url}")
    
    provider = OpenAIAdapter(api_key=openai_key, model=model, base_url=base_url)
    
    # 演示任务：创建并修改一个文件
    print("\n" + "=" * 60)
    print("演示任务：创建并修改文件")
    print("=" * 60)
    
    messages = [
        Message(
            role="user",
            content=(
                "请完成以下任务：\n"
                "1. 用 write 工具创建一个文件 demo.txt，内容是 'Hello, World!'\n"
                "2. 用 read 工具读取这个文件，确认内容正确\n"
                "3. 用 edit 工具将 'World' 替换为 'mini-pi'"
            )
        )
    ]
    
    try:
        agent_loop(provider, messages, max_steps=10)
        
        print("\n" + "=" * 60)
        print("✅ 演示完成！")
        print("=" * 60)
        
        # 验证结果
        if os.path.exists("demo.txt"):
            with open("demo.txt", 'r') as f:
                content = f.read()
            print(f"\n📄 最终文件内容: {content}")
            
            # 清理
            os.remove("demo.txt")
            print("🗑️  已清理测试文件")
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
