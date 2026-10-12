#!/usr/bin/env node
/**
 * 第 1 章：最小 Agent 循环
 * 
 * 核心概念：
 * 1. 维护一个消息数组（messages）
 * 2. 循环调用 LLM，直到没有工具调用为止
 * 3. 执行工具（bash），将结果追加到消息数组
 * 4. 防止无限循环（最大步数限制）
 */

import Anthropic from '@anthropic-ai/sdk';
import type { 
  MessageParam, 
  ContentBlock, 
  ToolUseBlock, 
  Tool,
  ToolResultBlockParam 
} from '@anthropic-ai/sdk/resources/messages';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

// ============ 类型定义 ============

// 使用 Anthropic SDK 的类型
type Message = MessageParam;

// ============ 工具定义 ============

const bashTool: Tool = {
  name: 'bash',
  description: '执行 bash 命令。可以用来查看文件、运行程序、操作文件系统等。',
  input_schema: {
    type: 'object' as const,
    properties: {
      command: {
        type: 'string',
        description: '要执行的 bash 命令'
      }
    },
    required: ['command']
  }
};

// ============ 工具执行 ============

/**
 * 执行 bash 工具
 * 
 * @param command - 要执行的命令
 * @returns 命令输出或错误信息
 */
async function executeBash(command: string): Promise<{ output: string; isError: boolean }> {
  console.log(`\n🔧 执行命令: ${command}`);
  
  try {
    const { stdout, stderr } = await execAsync(command, {
      timeout: 30000, // 30秒超时
      maxBuffer: 1024 * 1024 // 1MB 输出限制
    });
    
    const output = stdout + stderr;
    const truncated = output.length > 10000 ? output.slice(0, 10000) + '\n...(输出被截断)' : output;
    
    console.log(`✅ 命令完成，输出长度: ${output.length} 字节`);
    return { output: truncated, isError: false };
    
  } catch (error: any) {
    console.log(`❌ 命令失败: ${error.message}`);
    return { 
      output: `错误: ${error.message}\n${error.stdout || ''}${error.stderr || ''}`, 
      isError: true 
    };
  }
}

/**
 * 执行单个工具调用
 */
async function executeToolCall(toolUse: ToolUseBlock): Promise<ToolResultBlockParam> {
  if (toolUse.name === 'bash') {
    const command = (toolUse.input as any).command as string;
    const { output, isError } = await executeBash(command);
    
    const result: ToolResultBlockParam = {
      type: 'tool_result',
      tool_use_id: toolUse.id,
      content: output,
      is_error: isError
    };
    return result;
  }
  
  // 未知工具
  const errorResult: ToolResultBlockParam = {
    type: 'tool_result',
    tool_use_id: toolUse.id,
    content: `错误: 未知工具 '${toolUse.name}'`,
    is_error: true
  };
  return errorResult;
}

// ============ Agent 主循环 ============

/**
 * Agent 主循环：不断调用 LLM 并执行工具，直到没有更多工具调用
 * 
 * @param client - Anthropic 客户端
 * @param messages - 消息历史
 * @param maxSteps - 最大循环步数（防止无限循环）
 */
async function agentLoop(
  client: Anthropic,
  messages: Message[],
  maxSteps: number = 10
): Promise<void> {
  
  for (let step = 0; step < maxSteps; step++) {
    console.log(`\n${'='.repeat(60)}`);
    console.log(`第 ${step + 1} 步 / 最多 ${maxSteps} 步`);
    console.log('='.repeat(60));
    
    // 1. 调用 LLM
    console.log('\n🤖 正在调用模型...');
    const response = await client.messages.create({
      model: 'claude-3-5-sonnet-20241022',
      max_tokens: 4096,
      system: '你是一个有帮助的编程助手。使用 bash 工具来完成用户的请求。',
      tools: [bashTool],
      messages: messages
    });
    
    console.log(`📝 模型响应 (stop_reason: ${response.stop_reason})`);
    
    // 2. 将 assistant 消息追加到历史
    const assistantMessage: Message = {
      role: 'assistant' as const,
      content: response.content as ContentBlock[]
    };
    messages.push(assistantMessage);
    
    // 3. 打印文本内容
    const textBlocks = response.content.filter(block => block.type === 'text');
    if (textBlocks.length > 0) {
      console.log('\n💬 模型说:');
      textBlocks.forEach(block => {
        if (block.type === 'text') {
          console.log(block.text);
        }
      });
    }
    
    // 4. 检查是否有工具调用
    const toolUses = response.content.filter(
      block => block.type === 'tool_use'
    ) as ToolUseBlock[];
    
    if (toolUses.length === 0) {
      // 没有工具调用，循环结束
      console.log('\n✨ 没有更多工具调用，任务完成！');
      break;
    }
    
    // 5. 执行所有工具调用
    console.log(`\n🔨 执行 ${toolUses.length} 个工具调用...`);
    const toolResults: ToolResultBlockParam[] = [];
    
    for (const toolUse of toolUses) {
      const result = await executeToolCall(toolUse);
      toolResults.push(result);
    }
    
    // 6. 将工具结果追加到消息历史
    // 注意：Anthropic API 要求工具结果必须在 user 角色的消息中
    const userMessage: Message = {
      role: 'user' as const,
      content: toolResults
    };
    messages.push(userMessage);
    
    // 7. 继续下一轮循环
  }
  
  // 如果达到最大步数，说明可能陷入了循环
  if (messages.length >= maxSteps * 2) {
    console.log('\n⚠️  达到最大步数限制，强制停止');
  }
}

// ============ 主程序 ============

async function main() {
  // 检查 API key
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.error('错误: 请设置环境变量 ANTHROPIC_API_KEY');
    console.error('示例: export ANTHROPIC_API_KEY="your-api-key"');
    process.exit(1);
  }
  
  // 创建 Anthropic 客户端
  const client = new Anthropic({ apiKey });
  
  // 初始消息
  const messages: Message[] = [
    {
      role: 'user' as const,
      content: '请用 ls 命令查看当前目录的内容，然后告诉我有哪些文件。'
    }
  ];
  
  console.log('🚀 mini-pi Agent 启动！');
  console.log('📋 用户请求:', messages[0].content);
  
  try {
    await agentLoop(client, messages, 10);
    
    console.log('\n' + '='.repeat(60));
    console.log('✅ Agent 执行完成！');
    console.log(`📊 总消息数: ${messages.length}`);
    
  } catch (error) {
    console.error('\n❌ 发生错误:', error);
    process.exit(1);
  }
}

// 运行主程序
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { agentLoop, executeBash, bashTool };
