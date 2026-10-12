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
from openai import OpenAI

# ============ 工具定义 ============

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "执行 bash 命令。可以用来查看文件、运行程序、操作文件系统等。",
        "parameters": {
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
}

# ============ 工具执行 ============

def execute_bash(command: str) -> tuple[str, bool]:
    """执行 bash 工具，返回 (output, is_error)"""
    print(f"\n🔧 执行命令: {command}")
    
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        
        # 截断过长的输出
        if len(output) > 10000:
            output = output[:10000] + "\n...(输出被截断)"
        
        print(f"✅ 命令完成，输出长度: {len(output)} 字节")
        return output, result.returncode != 0
        
    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时（30秒）", True
    except Exception as e:
        return f"错误: {str(e)}", True

# ============ Agent 主循环 ============

def agent_loop(client: OpenAI, messages: List[Dict[str, Any]], max_steps: int = 10) -> None:
    """Agent 主循环：不断调用 LLM 并执行工具，直到没有更多工具调用"""
    
    for step in range(max_steps):
        print(f"\n{'=' * 60}\n第 {step + 1} 步 / 最多 {max_steps} 步\n{'=' * 60}")
        
        # 1. 调用 OpenAI API
        print("\n🤖 正在调用模型...")
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=[BASH_TOOL]
        )
        
        message = response.choices[0].message
        print(f"📝 模型响应 (finish_reason: {response.choices[0].finish_reason})")
        
        # 2. 将 assistant 消息追加到历史
        assistant_message = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": message.tool_calls
        }
        messages.append(assistant_message)
        
        # 3. 打印文本内容
        if message.content:
            print(f"\n💬 模型说:\n{message.content}")
        
        # 4. 检查是否有工具调用
        if not message.tool_calls:
            print("\n✨ 没有更多工具调用，任务完成！")
            break
        
        # 5. 执行所有工具调用
        print(f"\n🔨 执行 {len(message.tool_calls)} 个工具调用...")
        for tool_call in message.tool_calls:
            # 解析参数并执行
            import json
            args = json.loads(tool_call.function.arguments)
            output, is_error = execute_bash(args["command"])
            
            # 6. 将工具结果追加到消息历史
            # OpenAI 格式：role="tool", tool_call_id=xxx, content=结果
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": output
            }
            messages.append(tool_message)
        
        # 7. 继续下一轮循环
    
    if len(messages) >= max_steps * 2:
        print("\n⚠️  达到最大步数限制，强制停止")

# ============ 主程序 ============

def main():
    # 检查 API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("错误: 请设置环境变量 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    
    # 创建 OpenAI 客户端（支持自定义 base_url）
    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL")
    )
    
    # 初始消息
    messages = [
        {
            "role": "system",
            "content": "你是一个有帮助的编程助手。使用 bash 工具来完成用户的请求。"
        },
        {
            "role": "user",
            "content": "请用 ls 命令查看当前目录的内容，然后告诉我有哪些文件。"
        }
    ]
    
    print("🚀 mini-pi Agent 启动！")
    print(f"📋 用户请求: {messages[1]['content']}")
    
    try:
        agent_loop(client, messages, max_steps=10)
        print(f"\n{'=' * 60}\n✅ Agent 执行完成！\n📊 总消息数: {len(messages)}")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
