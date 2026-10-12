#!/usr/bin/env python3
"""
第 1 章：最小 Agent 循环

核心概念：
1. 维护一个消息数组（messages）
2. 循环调用 LLM，直到没有工具调用为止
3. 执行工具（bash），将结果追加到消息数组
4. 防止无限循环（最大步数限制）
"""

import os
import sys
import subprocess
from typing import List, Dict, Any
from anthropic import Anthropic

# ============ 工具定义 ============

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

# ============ 工具执行 ============

def execute_bash(command: str) -> tuple[str, bool]:
    """
    执行 bash 工具
    
    Args:
        command: 要执行的命令
        
    Returns:
        (output, is_error): 命令输出和是否出错
    """
    print(f"\n🔧 执行命令: {command}")
    
    try:
        # 执行命令，30秒超时
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # 合并 stdout 和 stderr
        output = result.stdout + result.stderr
        
        # 截断过长的输出（保护上下文窗口）
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

def execute_tool_call(tool_use: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个工具调用"""
    if tool_use["name"] == "bash":
        command = tool_use["input"]["command"]
        output, is_error = execute_bash(command)
        
        return {
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": output,
            "is_error": is_error
        }
    
    # 未知工具
    return {
        "type": "tool_result",
        "tool_use_id": tool_use["id"],
        "content": f"错误: 未知工具 '{tool_use['name']}'",
        "is_error": True
    }

# ============ Agent 主循环 ============

def agent_loop(client: Anthropic, messages: List[Dict[str, Any]], max_steps: int = 10) -> None:
    """
    Agent 主循环：不断调用 LLM 并执行工具，直到没有更多工具调用
    
    Args:
        client: Anthropic 客户端
        messages: 消息历史
        max_steps: 最大循环步数（防止无限循环）
    """
    
    for step in range(max_steps):
        print(f"\n{'=' * 60}")
        print(f"第 {step + 1} 步 / 最多 {max_steps} 步")
        print('=' * 60)
        
        # 1. 调用 LLM
        print("\n🤖 正在调用模型...")
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            system="你是一个有帮助的编程助手。使用 bash 工具来完成用户的请求。",
            tools=[BASH_TOOL],
            messages=messages
        )
        
        print(f"📝 模型响应 (stop_reason: {response.stop_reason})")
        
        # 2. 将 assistant 消息追加到历史
        assistant_message = {
            "role": "assistant",
            "content": response.content
        }
        messages.append(assistant_message)
        
        # 3. 打印文本内容
        text_blocks = [block for block in response.content if block.type == "text"]
        if text_blocks:
            print("\n💬 模型说:")
            for block in text_blocks:
                print(block.text)
        
        # 4. 检查是否有工具调用
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        
        if not tool_uses:
            # 没有工具调用，循环结束
            print("\n✨ 没有更多工具调用，任务完成！")
            break
        
        # 5. 执行所有工具调用
        print(f"\n🔨 执行 {len(tool_uses)} 个工具调用...")
        tool_results = []
        
        for tool_use in tool_uses:
            # 将 Anthropic 的 ContentBlock 对象转为字典
            tool_dict = {
                "type": tool_use.type,
                "id": tool_use.id,
                "name": tool_use.name,
                "input": tool_use.input
            }
            result = execute_tool_call(tool_dict)
            tool_results.append(result)
        
        # 6. 将工具结果追加到消息历史
        # 注意：Anthropic API 要求工具结果必须在 user 角色的消息中
        user_message = {
            "role": "user",
            "content": tool_results
        }
        messages.append(user_message)
        
        # 7. 继续下一轮循环
    
    # 如果达到最大步数，说明可能陷入了循环
    if len(messages) >= max_steps * 2:
        print("\n⚠️  达到最大步数限制，强制停止")

# ============ 主程序 ============

def main():
    # 检查 API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("错误: 请设置环境变量 ANTHROPIC_API_KEY", file=sys.stderr)
        print("示例: export ANTHROPIC_API_KEY='your-api-key'", file=sys.stderr)
        sys.exit(1)
    
    # 创建 Anthropic 客户端
    client = Anthropic(api_key=api_key)
    
    # 初始消息
    messages = [
        {
            "role": "user",
            "content": "请用 ls 命令查看当前目录的内容，然后告诉我有哪些文件。"
        }
    ]
    
    print("🚀 mini-pi Agent 启动！")
    print(f"📋 用户请求: {messages[0]['content']}")
    
    try:
        agent_loop(client, messages, max_steps=10)
        
        print("\n" + "=" * 60)
        print("✅ Agent 执行完成！")
        print(f"📊 总消息数: {len(messages)}")
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
