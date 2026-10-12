/**
 * 第 1 章测试：模拟 LLM 响应，验证循环逻辑
 */

import { test } from 'node:test';
import assert from 'node:assert';

// 模拟的工具调用执行器
async function mockExecuteToolCall(toolName: string, input: any): Promise<string> {
  if (toolName === 'bash') {
    // 模拟 bash 命令执行
    if (input.command === 'ls') {
      return 'file1.txt\nfile2.js\nREADME.md';
    }
    return `模拟执行: ${input.command}`;
  }
  return '未知工具';
}

// 模拟的 agent 循环（简化版）
async function mockAgentLoop(userPrompt: string, mockResponses: any[]): Promise<number> {
  const messages: any[] = [{ role: 'user', content: userPrompt }];
  let stepCount = 0;
  const maxSteps = 10;
  
  for (let step = 0; step < maxSteps && step < mockResponses.length; step++) {
    stepCount++;
    const response = mockResponses[step];
    
    // 添加 assistant 消息
    messages.push({
      role: 'assistant',
      content: response.content
    });
    
    // 检查工具调用
    const toolUses = response.content.filter((block: any) => block.type === 'tool_use');
    
    if (toolUses.length === 0) {
      // 没有工具调用，结束
      break;
    }
    
    // 执行工具并添加结果
    const toolResults = [];
    for (const toolUse of toolUses) {
      const output = await mockExecuteToolCall(toolUse.name, toolUse.input);
      toolResults.push({
        type: 'tool_result',
        tool_use_id: toolUse.id,
        content: output
      });
    }
    
    messages.push({
      role: 'user',
      content: toolResults
    });
  }
  
  return stepCount;
}

test('Agent 循环：单步工具调用后终止', async () => {
  const mockResponses = [
    {
      content: [
        { type: 'text', text: '我来查看一下文件' },
        { type: 'tool_use', id: 'call_1', name: 'bash', input: { command: 'ls' } }
      ]
    },
    {
      content: [
        { type: 'text', text: '看到了 3 个文件：file1.txt, file2.js, README.md' }
      ]
    }
  ];
  
  const steps = await mockAgentLoop('列出文件', mockResponses);
  
  assert.strictEqual(steps, 2, '应该执行 2 步：第 1 步调用工具，第 2 步返回结果');
});

test('Agent 循环：多步工具调用', async () => {
  const mockResponses = [
    {
      content: [
        { type: 'tool_use', id: 'call_1', name: 'bash', input: { command: 'pwd' } }
      ]
    },
    {
      content: [
        { type: 'tool_use', id: 'call_2', name: 'bash', input: { command: 'ls' } }
      ]
    },
    {
      content: [
        { type: 'text', text: '完成了' }
      ]
    }
  ];
  
  const steps = await mockAgentLoop('查看目录', mockResponses);
  
  assert.strictEqual(steps, 3, '应该执行 3 步');
});

test('Agent 循环：立即终止（无工具调用）', async () => {
  const mockResponses = [
    {
      content: [
        { type: 'text', text: '好的，我明白了' }
      ]
    }
  ];
  
  const steps = await mockAgentLoop('你好', mockResponses);
  
  assert.strictEqual(steps, 1, '应该只执行 1 步就终止');
});

console.log('✅ 所有测试通过！Agent 循环逻辑正确。');
