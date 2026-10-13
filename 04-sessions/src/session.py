"""
第 4 章：会话存储（对标 pi 的 packages/coding-agent/src/core/session-manager.ts）

- 一个会话 = 一个 JSONL 文件：第一行是 header，之后每行一个 entry
- 每个 entry 有 id / parentId，构成一棵树；leaf 指针指向"当前位置"
- 只追加（append-only），分支 = 移动 leaf 指针，旧内容永不修改
- 发给 LLM 的上下文由 entries "投影"出来（build_context）

消息直接使用 OpenAI Chat Completions 格式：
  {"role": "user", "content": "..."}
  {"role": "assistant", "content": "...", "tool_calls": [...]}
  {"role": "tool", "tool_call_id": "...", "content": "..."}
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

SESSION_VERSION = 1

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(existing: dict) -> str:
    # pi 用 8 位短 id，冲突时重试
    while True:
        i = uuid.uuid4().hex[:8]
        if i not in existing:
            return i


def entry_to_messages(entry: dict) -> list[dict]:
    """把一个 entry 投影成 0~N 条 LLM 消息（对应 pi 的 sessionEntryToContextMessages）"""
    t = entry["type"]
    if t == "message":
        return [entry["message"]]
    if t == "compaction":
        return [{"role": "user",
                 "content": COMPACTION_SUMMARY_PREFIX + entry["summary"] + COMPACTION_SUMMARY_SUFFIX}]
    if t == "branch_summary" and entry.get("summary"):
        return [{"role": "user",
                 "content": BRANCH_SUMMARY_PREFIX + entry["summary"] + BRANCH_SUMMARY_SUFFIX}]
    # model_change / label 等：只记录状态，不进入上下文
    return []


class SessionManager:
    def __init__(self, path: Optional[str], cwd: str = "."):
        self.path = path
        self.entries: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.leaf_id: Optional[str] = None
        self.header: dict = {}
        self._needs_separator = False
        if path and os.path.exists(path):
            self._load()
        else:
            self.header = {"type": "session", "version": SESSION_VERSION,
                           "id": uuid.uuid4().hex, "timestamp": _now(), "cwd": os.path.abspath(cwd)}
            if path:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(self.header, ensure_ascii=False) + "\n")

    # ---------- 创建 / 恢复 ----------

    @classmethod
    def create(cls, session_dir: str, cwd: str = ".") -> "SessionManager":
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        sid = uuid.uuid4().hex[:12]
        return cls(os.path.join(session_dir, f"{stamp}_{sid}.jsonl"), cwd)

    @classmethod
    def open(cls, path: str) -> "SessionManager":
        return cls(path)

    @classmethod
    def continue_recent(cls, session_dir: str, cwd: str = ".") -> "SessionManager":
        """恢复最近修改的会话（对应 pi 的 SessionManager.continueRecent / `pi -c`）"""
        files = []
        if os.path.isdir(session_dir):
            files = [os.path.join(session_dir, f) for f in os.listdir(session_dir) if f.endswith(".jsonl")]
        if not files:
            return cls.create(session_dir, cwd)
        return cls.open(max(files, key=os.path.getmtime))

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                self._needs_separator = not line.endswith("\n")
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 和 pi 一样：跳过损坏的行（例如写到一半崩溃）
                if obj.get("type") == "session":
                    self.header = obj
                    continue
                self.entries.append(obj)
                self.by_id[obj["id"]] = obj
        # 恢复时 leaf = 文件中最后一个 entry
        self.leaf_id = self.entries[-1]["id"] if self.entries else None

    # ---------- 追加 ----------

    def _append(self, entry: dict) -> str:
        entry.setdefault("id", _new_id(self.by_id))
        entry.setdefault("parentId", self.leaf_id)
        entry.setdefault("timestamp", _now())
        self.entries.append(entry)
        self.by_id[entry["id"]] = entry
        self.leaf_id = entry["id"]
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                # 保留损坏的尾行，用换行隔开下一条完整 entry。
                if self._needs_separator:
                    f.write("\n")
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._needs_separator = False
        return entry["id"]

    def append_message(self, message: dict) -> str:
        return self._append({"type": "message", "message": message})

    def append_model_change(self, provider: str, model_id: str) -> str:
        return self._append({"type": "model_change", "provider": provider, "modelId": model_id})

    def append_compaction(self, summary: str, first_kept_entry_id: str, tokens_before: int,
                          details: Optional[dict] = None) -> str:
        return self._append({"type": "compaction", "summary": summary,
                             "firstKeptEntryId": first_kept_entry_id,
                             "tokensBefore": tokens_before, "details": details or {}})

    # ---------- 树 / 分支 ----------

    def branch(self, from_id: str) -> None:
        """把 leaf 移到更早的 entry；下一次 append 会成为它的新孩子"""
        if from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found")
        self.leaf_id = from_id

    def branch_with_summary(self, from_id: Optional[str], summary: str) -> str:
        if from_id is not None and from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found")
        old_leaf = self.leaf_id or "root"
        self.leaf_id = from_id
        return self._append({"type": "branch_summary", "fromId": old_leaf, "summary": summary})

    def get_branch(self, leaf_id: Optional[str] = None) -> list[dict]:
        """从 leaf 沿 parentId 走到根，再反转：得到当前分支（根 → leaf）"""
        cur = self.by_id.get(leaf_id or self.leaf_id) if (leaf_id or self.leaf_id) else None
        path = []
        while cur:
            path.append(cur)
            cur = self.by_id.get(cur["parentId"]) if cur.get("parentId") else None
        return list(reversed(path))

    def get_children(self, entry_id: Optional[str]) -> list[dict]:
        return [e for e in self.entries if e.get("parentId") == entry_id]

    def render_tree(self) -> str:
        lines: list[str] = []

        def walk(parent: Optional[str], depth: int) -> None:
            for e in self.get_children(parent):
                mark = " ← leaf" if e["id"] == self.leaf_id else ""
                lines.append(f"{'  ' * depth}- [{e['id']}] {describe(e)}{mark}")
                walk(e["id"], depth + 1)

        walk(None, 0)
        return "\n".join(lines)

    # ---------- 上下文投影 ----------

    def build_context_entries(self) -> list[dict]:
        """对应 pi 的 buildContextEntries：
        没有压缩 → 整条分支；
        有压缩   → [最新的 compaction] + [firstKeptEntryId 起、compaction 之前的 entry] + [compaction 之后的 entry]
        """
        path = self.get_branch()
        idx = max((i for i, e in enumerate(path) if e["type"] == "compaction"), default=-1)
        if idx < 0:
            return path
        comp = path[idx]
        out = [comp]
        found = False
        for e in path[:idx]:
            if e["id"] == comp["firstKeptEntryId"]:
                found = True
            if found:
                out.append(e)
        out.extend(path[idx + 1:])
        return out

    def build_context(self) -> list[dict]:
        msgs: list[dict] = []
        for i, e in enumerate(self.build_context_entries()):
            # 旧的 compaction 可能落在保留区间内，只有位于开头（最新）的那个才生效
            if e["type"] == "compaction" and i > 0:
                continue
            msgs.extend(entry_to_messages(e))
        return msgs


def describe(e: dict) -> str:
    t = e["type"]
    if t == "message":
        m = e["message"]
        text = m.get("content") or ""
        if m.get("tool_calls"):
            text = (text + " " if text else "") + "调用 " + ", ".join(
                tc["function"]["name"] for tc in m["tool_calls"])
        return f"{m['role']}: {str(text)[:40]}"
    if t == "compaction":
        return f"compaction (tokensBefore={e['tokensBefore']})"
    if t == "branch_summary":
        return f"branch_summary from {e['fromId']}"
    return t
