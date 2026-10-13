"""第 2 章的统一消息与 Provider 接口，供后续章节复用。"""

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
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # 当 role == "tool" 时使用
    stop_reason: str | None = None  # length / max_tokens 时禁止执行残缺调用

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
        stream: bool = False,
        max_tokens: int | None = None
    ) -> Message | Iterator[dict]:
        """
        发送请求，返回 Message 或流式事件

        Args:
            messages: 统一格式的消息历史
            tools: 工具定义（JSON Schema 格式）
            stream: 是否流式输出
            max_tokens: 可选输出预算，用于后续章节的摘要请求

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
