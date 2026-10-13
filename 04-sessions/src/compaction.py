"""
第 4 章：上下文压缩（对标 pi 的 packages/coding-agent/src/core/compaction/）

流程：
  1. should_compact：contextTokens > contextWindow - reserveTokens 时触发
  2. prepare_compaction：从最新消息往回累计 keepRecentTokens，找切点
     - 切点永远不落在 tool 结果上（tool 结果必须紧跟它的 tool_call）
     - 切点落在一轮对话中间时，这一轮的前半段单独做"turn prefix"摘要
  3. compact：用当前模型生成摘要；已有旧摘要时用 UPDATE 提示词增量更新
  4. 把 compaction entry 追加到会话；之后 build_context 自动投影成"摘要 + 最近消息"
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from prompts import (
    BRANCH_SUMMARY_PROMPT,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SYSTEM_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
)
from session import SessionManager, entry_to_messages
from tokens import estimate_message_tokens, estimate_messages_tokens

TOOL_RESULT_MAX_CHARS = 2000


@dataclass
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16384      # pi 默认值
    keep_recent_tokens: int = 20000  # pi 默认值


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    if not settings.enabled:
        return False
    return context_tokens > context_window - settings.reserve_tokens


# ---------- 序列化：把对话变成纯文本，防止模型"接着聊" ----------

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... {len(text) - max_chars} more characters truncated]"


def serialize_conversation(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user":
            if content:
                parts.append(f"[User]: {content}")
        elif role == "assistant":
            if content:
                parts.append(f"[Assistant]: {content}")
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                args = fn.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
                arg_str = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
                calls.append(f"{fn['name']}({arg_str})")
            if calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(calls)}")
        elif role == "tool":
            if content:
                parts.append(f"[Tool result]: {_truncate(content, TOOL_RESULT_MAX_CHARS)}")
    return "\n\n".join(parts)


# ---------- 文件操作追踪（附加在摘要末尾） ----------

def extract_file_ops(messages: list[dict], read: set, modified: set) -> None:
    for m in messages:
        for tc in m.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            path = args.get("path")
            if not isinstance(path, str):
                continue
            if fn["name"] == "read":
                read.add(path)
            elif fn["name"] in ("write", "edit"):
                modified.add(path)


def format_file_ops(read: set, modified: set) -> str:
    read_only = sorted(read - modified)
    sections = []
    if read_only:
        sections.append("<read-files>\n" + "\n".join(read_only) + "\n</read-files>")
    if modified:
        sections.append("<modified-files>\n" + "\n".join(sorted(modified)) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections) if sections else ""


# ---------- 切点选择 ----------

def _entry_messages(e: dict) -> list[dict]:
    return [] if e["type"] == "compaction" else entry_to_messages(e)


def is_cut_point(e: dict) -> bool:
    """可以作为"保留区第一条"的 entry：不能是 tool 结果"""
    return any(m.get("role") != "tool" for m in _entry_messages(e))


def is_turn_start(e: dict) -> bool:
    """一轮对话的开始：user 消息或分支摘要（也是 user 角色）"""
    return any(m.get("role") == "user" for m in _entry_messages(e))


@dataclass
class CutPoint:
    first_kept_index: int
    turn_start_index: int
    is_split_turn: bool


def find_cut_point(entries: list[dict], start: int, end: int, keep_recent_tokens: int) -> CutPoint:
    cut_points = [i for i in range(start, end) if is_cut_point(entries[i])]
    if not cut_points:
        return CutPoint(start, -1, False)

    acc = 0
    cut = cut_points[0]
    for i in range(end - 1, start - 1, -1):
        t = sum(estimate_message_tokens(m) for m in _entry_messages(entries[i]))
        if t == 0:
            continue
        acc += t
        if acc >= keep_recent_tokens:
            # 取 i 处或之后最近的合法切点；如果尾部的 tool 结果本身就超预算，
            # 就退到最后一个切点（它们前面那条 assistant tool_call）
            cut = next((c for c in cut_points if c >= i), cut_points[-1])
            break

    if is_turn_start(entries[cut]):
        return CutPoint(cut, -1, False)
    ts = next((i for i in range(cut, start - 1, -1) if is_turn_start(entries[i])), -1)
    return CutPoint(cut, ts, ts != -1)


@dataclass
class CompactionPreparation:
    first_kept_entry_id: str
    messages_to_summarize: list[dict]
    turn_prefix_messages: list[dict]
    is_split_turn: bool
    tokens_before: int
    previous_summary: Optional[str]
    read_files: set = field(default_factory=set)
    modified_files: set = field(default_factory=set)


def prepare_compaction(session: SessionManager, settings: CompactionSettings) -> Optional[CompactionPreparation]:
    path = session.get_branch()
    if path and path[-1]["type"] == "compaction":
        return None  # 刚压缩过，没有新内容

    entries = session.build_context_entries()
    previous_summary = None
    start = 0
    read: set = set()
    modified: set = set()
    if entries and entries[0]["type"] == "compaction":
        prev = entries[0]
        previous_summary = prev["summary"]
        start = 1
        d = prev.get("details") or {}
        read.update(d.get("readFiles", []))
        modified.update(d.get("modifiedFiles", []))

    tokens_before = estimate_messages_tokens(session.build_context())
    cp = find_cut_point(entries, start, len(entries), settings.keep_recent_tokens)
    if cp.first_kept_index >= len(entries):
        return None
    history_end = cp.turn_start_index if cp.is_split_turn else cp.first_kept_index

    def msgs(a: int, b: int) -> list[dict]:
        out = []
        for e in entries[a:b]:
            out.extend(_entry_messages(e))
        return out

    to_sum = msgs(start, history_end)
    prefix = msgs(cp.turn_start_index, cp.first_kept_index) if cp.is_split_turn else []
    if not to_sum and not prefix:
        return None
    extract_file_ops(to_sum + prefix, read, modified)
    return CompactionPreparation(entries[cp.first_kept_index]["id"], to_sum, prefix,
                                 cp.is_split_turn, tokens_before, previous_summary, read, modified)


# ---------- 调用 LLM 生成摘要 ----------

def _complete(client: Any, model: str, prompt_text: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                  {"role": "user", "content": prompt_text}],
        max_tokens=max_tokens,
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        # 被截断的摘要不能当作检查点保存
        raise RuntimeError("Summarization failed: generation hit the token cap and the summary is incomplete")
    return choice.message.content or ""


def generate_summary(client: Any, model: str, messages: list[dict], reserve_tokens: int,
                     custom_instructions: Optional[str] = None,
                     previous_summary: Optional[str] = None) -> str:
    max_tokens = math.floor(0.8 * reserve_tokens)
    base = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base = f"{base}\n\nAdditional focus: {custom_instructions}"
    prompt = f"<conversation>\n{serialize_conversation(messages)}\n</conversation>\n\n"
    if previous_summary:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    prompt += base
    return _complete(client, model, prompt, max_tokens)


def generate_turn_prefix_summary(client: Any, model: str, messages: list[dict], reserve_tokens: int) -> str:
    max_tokens = math.floor(0.5 * reserve_tokens)
    prompt = (f"# Conversation\n{serialize_conversation(messages)}\n\n"
              f"# Instructions\n{TURN_PREFIX_SUMMARIZATION_PROMPT}")
    return _complete(client, model, prompt, max_tokens)


def compact(session: SessionManager, client: Any, model: str, settings: CompactionSettings,
            custom_instructions: Optional[str] = None) -> Optional[dict]:
    """执行一次压缩并写入会话；无可压缩内容时返回 None"""
    prep = prepare_compaction(session, settings)
    if prep is None:
        return None
    if prep.is_split_turn and prep.turn_prefix_messages:
        history = prep.previous_summary or "No prior history."
        if prep.messages_to_summarize:
            history = generate_summary(client, model, prep.messages_to_summarize, settings.reserve_tokens,
                                       custom_instructions, prep.previous_summary)
        prefix = generate_turn_prefix_summary(client, model, prep.turn_prefix_messages, settings.reserve_tokens)
        summary = f"{history}\n\n---\n\n**Turn Context (split turn):**\n\n{prefix}"
    else:
        summary = generate_summary(client, model, prep.messages_to_summarize, settings.reserve_tokens,
                                   custom_instructions, prep.previous_summary)
    summary += format_file_ops(prep.read_files, prep.modified_files)
    details = {"readFiles": sorted(prep.read_files - prep.modified_files),
               "modifiedFiles": sorted(prep.modified_files)}
    entry_id = session.append_compaction(summary, prep.first_kept_entry_id, prep.tokens_before, details)
    return {"id": entry_id, "summary": summary, "firstKeptEntryId": prep.first_kept_entry_id,
            "tokensBefore": prep.tokens_before,
            "tokensAfter": estimate_messages_tokens(session.build_context())}


def summarize_branch(client: Any, model: str, messages: list[dict],
                     custom_instructions: Optional[str] = None) -> str:
    """离开一个分支前，给它生成摘要（对标 branch-summarization.ts）"""
    instructions = BRANCH_SUMMARY_PROMPT
    if custom_instructions:
        instructions = f"{instructions}\n\nAdditional focus: {custom_instructions}"
    prompt = f"<conversation>\n{serialize_conversation(messages)}\n</conversation>\n\n{instructions}"
    return _complete(client, model, prompt, 4096)  # pi: Math.min(4096, model.maxTokens)


# ---------- 上下文溢出识别 ----------

OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "too many tokens",
)


def is_context_overflow(error: Exception) -> bool:
    text = str(error).lower()
    code = str(getattr(error, "code", "") or "").lower()
    return code == "context_length_exceeded" or any(m in text for m in OVERFLOW_MARKERS)
