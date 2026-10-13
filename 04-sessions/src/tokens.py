"""
Token 估算（对标 pi compaction.ts 中的 estimateTokens：chars / 4）

pi 的 chars/4 对英文和代码大致合理，但中文一个汉字通常 ≈ 1 个甚至更多 token，
chars/4 会把中文低估约 4 倍。这里实现一个 CJK 感知的估算器：
  - CJK 字符（中日韩、全角标点）：每个字符算 1 token
  - 其他字符：每 4 个字符算 1 token
安装了 tiktoken 时可以选用精确计数。
"""

from __future__ import annotations

import json
import math


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF      # CJK 统一表意文字
        or 0x3400 <= cp <= 0x4DBF   # 扩展 A
        or 0x20000 <= cp <= 0x2FFFF # 扩展 B 及之后
        or 0xF900 <= cp <= 0xFAFF   # 兼容表意文字
        or 0x3000 <= cp <= 0x303F   # CJK 标点
        or 0x3040 <= cp <= 0x30FF   # 日文假名
        or 0xAC00 <= cp <= 0xD7AF   # 韩文音节
        or 0xFF00 <= cp <= 0xFFEF   # 全角字符
    )


def naive_estimate(text: str) -> int:
    """pi 的做法：Math.ceil(chars / 4)"""
    return math.ceil(len(text) / 4)


def estimate_text_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if is_cjk(ch))
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


_encoder = None


def tiktoken_count(text: str, model: str = "gpt-4o-mini") -> int:
    """可选：用 tiktoken 精确计数（未安装时抛 ImportError）"""
    global _encoder
    if _encoder is None:
        import tiktoken
        try:
            _encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            _encoder = tiktoken.get_encoding("o200k_base")
    return len(_encoder.encode(text))


def message_text(msg: dict) -> str:
    """把一条 OpenAI 格式消息里会占 token 的文本拼起来"""
    parts = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts.extend(b.get("text", "") for b in content if isinstance(b, dict))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        parts.append(fn.get("name", "") + (fn.get("arguments") if isinstance(fn.get("arguments"), str)
                                           else json.dumps(fn.get("arguments"), ensure_ascii=False)))
    return "".join(parts)


def estimate_message_tokens(msg: dict, use_tiktoken: bool = False) -> int:
    text = message_text(msg)
    if use_tiktoken:
        try:
            return tiktoken_count(text)
        except ImportError:
            pass
    return estimate_text_tokens(text)


def estimate_messages_tokens(msgs: list[dict], use_tiktoken: bool = False) -> int:
    return sum(estimate_message_tokens(m, use_tiktoken) for m in msgs)
