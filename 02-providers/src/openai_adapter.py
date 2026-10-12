"""
OpenAI 适配器

将 OpenAI API 格式转换为统一的 Message 格式
"""

import json
from typing import Iterator, Any
from openai import OpenAI
from main import Provider, Message, ToolCall


class OpenAIAdapter(Provider):
    """OpenAI 模型适配器"""
    
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str | None = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
    
    def create_completion(
        self,
        messages: list[Message],
        tools: list[dict],
        stream: bool = False
    ) -> Message | Iterator[dict]:
        """调用 OpenAI API"""
        native_messages = self.to_native_messages(messages)
        openai_tools = self._convert_tools(tools)
        
        if stream:
            return self._stream_completion(native_messages, openai_tools)
        else:
            return self._blocking_completion(native_messages, openai_tools)
    
    def _blocking_completion(self, native_messages: list[dict], tools: list[dict]) -> Message:
        """非流式调用"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=native_messages,
            tools=tools if tools else None,
            temperature=0.7
        )
        
        return self.from_native_message(response.choices[0].message)
    
    def _stream_completion(self, native_messages: list[dict], tools: list[dict]) -> Iterator[dict]:
        """流式调用"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=native_messages,
            tools=tools if tools else None,
            temperature=0.7,
            stream=True
        )
        
        # 累积流式片段
        accumulated = {
            "content": "",
            "tool_calls": {}  # {index: {id, name, arguments}}
        }
        
        for chunk in response:
            if not chunk.choices:
                continue
            
            delta = chunk.choices[0].delta
            
            # 文本内容
            if delta.content:
                accumulated["content"] += delta.content
                yield {"type": "text_delta", "text": delta.content}
            
            # 工具调用（需要累积，因为是增量的）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    index = tc_delta.index
                    
                    if index not in accumulated["tool_calls"]:
                        accumulated["tool_calls"][index] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": ""
                        }
                    
                    tc = accumulated["tool_calls"][index]
                    
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        tc["name"] += tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tc["arguments"] += tc_delta.function.arguments
                    
                    yield {"type": "tool_call_delta", "index": index}
        
        # 构建最终消息
        final_message = self._build_final_message(accumulated)
        yield {"type": "done", "message": final_message}
    
    def _build_final_message(self, accumulated: dict) -> Message:
        """从累积的数据构建最终消息"""
        content = accumulated["content"] if accumulated["content"] else None
        
        tool_calls = None
        if accumulated["tool_calls"]:
            tool_calls = []
            for tc_data in accumulated["tool_calls"].values():
                try:
                    arguments = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError:
                    arguments = {"_raw": tc_data["arguments"]}
                
                tool_calls.append(ToolCall(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=arguments
                ))
        
        return Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls
        )
    
    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        """将统一格式转换为 OpenAI 格式"""
        native = []
        
        for msg in messages:
            if msg.role == "tool":
                # OpenAI: 工具结果用 role="tool"
                native.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content or ""
                })
            elif msg.tool_calls:
                # OpenAI: tool_calls 是独立字段
                native.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                })
            else:
                # 普通消息
                native.append({
                    "role": msg.role,
                    "content": msg.content or ""
                })
        
        return native
    
    def from_native_message(self, native_msg: Any) -> Message:
        """将 OpenAI 格式转换为统一格式"""
        content = native_msg.content if native_msg.content else None
        
        tool_calls = None
        if native_msg.tool_calls:
            tool_calls = []
            for tc in native_msg.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": tc.function.arguments}
                
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments
                ))
        
        return Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls
        )
    
    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """将通用工具定义转换为 OpenAI 的格式"""
        openai_tools = []
        
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"]
                }
            })
        
        return openai_tools
