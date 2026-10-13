#!/usr/bin/env python3
"""第 4 章测试：会话存储 + 压缩（全部使用假的 LLM 客户端，不访问网络）"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import prompts
from compaction import (CompactionSettings, compact, find_cut_point, is_context_overflow,
                        prepare_compaction, serialize_conversation, should_compact, summarize_branch)
from main import Agent, parse_args
from session import SessionManager
from tokens import estimate_text_tokens, naive_estimate


# ---------- 假客户端 ----------

class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0) if self.replies else text_reply("默认摘要")
        if isinstance(r, Exception):
            raise r
        return r


def text_reply(text, finish="stop"):
    msg = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def tool_reply(call_id, command):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name="bash", arguments=json.dumps({"command": command})))
    msg = SimpleNamespace(content="", tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


def fake_client(replies):
    comp = FakeCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


def tool_call_msg(cid, name="bash", args=None):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {"command": "ls"})}}]}


# ---------- 会话存储 ----------

def test_jsonl_save_and_resume(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "你好"})
    s.append_message({"role": "assistant", "content": "hi"})
    lines = open(s.path, encoding="utf-8").read().splitlines()
    assert json.loads(lines[0])["type"] == "session"
    assert len(lines) == 3
    assert "你好" in lines[1]  # ensure_ascii=False，中文可读

    r = SessionManager.continue_recent(str(tmp_path))
    assert r.path == s.path
    assert r.leaf_id == s.leaf_id
    assert [m["content"] for m in r.build_context()] == ["你好", "hi"]
    r.append_message({"role": "user", "content": "继续"})
    assert r.get_branch()[-1]["parentId"] == s.leaf_id


def test_corrupt_line_skipped(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "a"})
    with open(s.path, "a") as f:
        f.write('{"type": "message", "id": "broken"\n')
    assert len(SessionManager.open(s.path).entries) == 1


def test_resume_after_incomplete_tail_can_persist_new_messages(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "原消息"})
    with open(s.path, "a", encoding="utf-8") as f:
        f.write('{"type": "message", "id": "broken"')
    resumed = SessionManager.open(s.path)
    resumed.append_message({"role": "assistant", "content": "恢复后的消息"})
    resumed.append_message({"role": "user", "content": "继续"})
    assert [m["content"] for m in SessionManager.open(s.path).build_context()] == [
        "原消息", "恢复后的消息", "继续"]


def test_branching_tree(tmp_path):
    s = SessionManager.create(str(tmp_path))
    u1 = s.append_message({"role": "user", "content": "q1"})
    s.append_message({"role": "assistant", "content": "a1"})
    s.append_message({"role": "user", "content": "q2-old"})
    s.branch(u1)
    s.append_message({"role": "assistant", "content": "a1-new"})
    ctx = [m["content"] for m in s.build_context()]
    assert ctx == ["q1", "a1-new"]
    assert len(s.get_children(u1)) == 2
    assert "q2-old" in s.render_tree()
    # 旧分支仍在文件里
    assert len(SessionManager.open(s.path).entries) == 4


def test_branch_with_summary(tmp_path):
    s = SessionManager.create(str(tmp_path))
    u1 = s.append_message({"role": "user", "content": "q1"})
    s.append_message({"role": "assistant", "content": "试了方案 A"})
    s.branch_with_summary(u1, "方案 A 失败")
    ctx = s.build_context()
    assert ctx[-1]["role"] == "user" and "方案 A 失败" in ctx[-1]["content"]
    assert ctx[-1]["content"].startswith("The following is a summary of a branch")


def test_model_change_not_in_context(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "q"})
    s.append_model_change("openai", "gpt-4o")
    assert len(s.build_context()) == 1


# ---------- token 估算 ----------

def test_cjk_estimator():
    zh = "这是一个用于测试的中文句子"  # 13 个汉字
    assert naive_estimate(zh) == 4          # pi 的 chars/4 严重低估
    assert estimate_text_tokens(zh) == 13
    assert estimate_text_tokens("abcdefgh") == 2
    assert estimate_text_tokens("ab中文") == 3


# ---------- 阈值 ----------

def test_should_compact():
    st = CompactionSettings()
    assert st.reserve_tokens == 16384 and st.keep_recent_tokens == 20000
    assert not should_compact(128000 - 16384, 128000, st)
    assert should_compact(128000 - 16383, 128000, st)
    assert not should_compact(10**9, 128000, CompactionSettings(enabled=False))


# ---------- 切点 ----------

# 每轮约 208 token；KEEP=208 时切点正好落在最后一轮的 user 消息上（不拆分轮次）
KEEP = 208


def _long_session(tmp_path, n_turns=6, size=400):
    s = SessionManager.create(str(tmp_path))
    for i in range(n_turns):
        s.append_message({"role": "user", "content": f"q{i} " + "x" * size})
        s.append_message(tool_call_msg(f"c{i}", "read", {"path": f"f{i}.py"}))
        s.append_message({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * size})
        s.append_message({"role": "assistant", "content": f"a{i}"})
    return s


def test_cut_point_never_on_tool_result(tmp_path):
    s = _long_session(tmp_path)
    entries = s.build_context_entries()
    for keep in range(1, 1200, 7):
        cp = find_cut_point(entries, 0, len(entries), keep)
        assert entries[cp.first_kept_index]["message"]["role"] != "tool"


def test_trailing_tool_result_over_budget_keeps_its_call(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "q"})
    s.append_message(tool_call_msg("c1"))
    s.append_message({"role": "tool", "tool_call_id": "c1", "content": "z" * 4000})
    entries = s.build_context_entries()
    cp = find_cut_point(entries, 0, len(entries), 100)
    assert cp.first_kept_index == 1
    assert cp.is_split_turn and cp.turn_start_index == 0


def test_compact_and_projection(tmp_path):
    s = _long_session(tmp_path)
    client, comp = fake_client([text_reply("## Goal\n摘要一")])
    res = compact(s, client, "m", CompactionSettings(keep_recent_tokens=KEEP))
    assert res and res["tokensAfter"] < res["tokensBefore"]
    call = comp.calls[0]
    assert call["messages"][0]["content"] == prompts.SUMMARIZATION_SYSTEM_PROMPT
    assert call["messages"][1]["content"].endswith(prompts.SUMMARIZATION_PROMPT)
    assert call["max_tokens"] == int(0.8 * 16384)
    ctx = s.build_context()
    assert ctx[0]["content"].startswith("The conversation history before this point was compacted")
    assert "<read-files>" in ctx[0]["content"]
    assert ctx[1]["role"] != "tool"
    # 保留下来的每个 tool 结果，其 tool_call 也在上下文里
    call_ids = {tc["id"] for m in ctx for tc in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in call_ids for m in ctx if m["role"] == "tool")
    # 刚压缩完不能再压缩
    assert prepare_compaction(s, CompactionSettings(keep_recent_tokens=KEEP)) is None


def test_repeat_compaction_uses_update_prompt(tmp_path):
    s = _long_session(tmp_path)
    client, comp = fake_client([text_reply("摘要一"), text_reply("摘要二")])
    st = CompactionSettings(keep_recent_tokens=KEEP)
    compact(s, client, "m", st)
    for i in range(4):
        s.append_message({"role": "user", "content": f"new{i} " + "x" * 400})
        s.append_message({"role": "assistant", "content": "ok"})
    compact(s, client, "m", st, custom_instructions="关注数据库迁移")
    p = next(c["messages"][1]["content"] for c in comp.calls[1:] if "<previous-summary>" in c["messages"][1]["content"])
    assert "<previous-summary>\n摘要一" in p
    assert prompts.UPDATE_SUMMARIZATION_INSTRUCTIONS in p
    assert p.endswith("Additional focus: 关注数据库迁移")
    ctx = s.build_context()
    assert "摘要二" in ctx[0]["content"] and "摘要一" not in ctx[0]["content"]
    # 文件追踪从旧压缩继承
    assert "f0.py" in ctx[0]["content"]


def test_split_turn_uses_turn_prefix_prompt(tmp_path):
    s = SessionManager.create(str(tmp_path))
    s.append_message({"role": "user", "content": "early " + "x" * 400})
    s.append_message({"role": "assistant", "content": "ok"})
    s.append_message({"role": "user", "content": "big task"})
    for i in range(5):
        s.append_message(tool_call_msg(f"c{i}"))
        s.append_message({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 400})
    client, comp = fake_client([text_reply("历史"), text_reply("前缀")])
    res = compact(s, client, "m", CompactionSettings(keep_recent_tokens=250))
    assert "**Turn Context (split turn):**" in res["summary"]
    assert prompts.TURN_PREFIX_SUMMARIZATION_PROMPT in comp.calls[1]["messages"][1]["content"]
    assert comp.calls[1]["max_tokens"] == int(0.5 * 16384)


def test_length_finish_is_rejected(tmp_path):
    s = _long_session(tmp_path)
    client, _ = fake_client([text_reply("半截", finish="length")])
    try:
        compact(s, client, "m", CompactionSettings(keep_recent_tokens=KEEP))
        assert False
    except RuntimeError as e:
        assert "incomplete" in str(e)
    assert all(e["type"] != "compaction" for e in s.entries)


def test_serialize_and_branch_summary():
    msgs = [{"role": "user", "content": "hi"}, tool_call_msg("c", "bash", {"command": "ls"}),
            {"role": "tool", "tool_call_id": "c", "content": "a" * 3000}]
    text = serialize_conversation(msgs)
    assert '[Assistant tool calls]: bash(command="ls")' in text
    assert "[... 1000 more characters truncated]" in text
    client, comp = fake_client([text_reply("branch")])
    summarize_branch(client, "m", msgs)
    assert comp.calls[0]["messages"][1]["content"].endswith(prompts.BRANCH_SUMMARY_PROMPT)


# ---------- Agent 集成 ----------

def test_agent_tool_loop_persists(tmp_path):
    s = SessionManager.create(str(tmp_path))
    client, _ = fake_client([tool_reply("c1", "echo hi"), text_reply("完成")])
    agent = Agent(client, "m", s, tool_runner=lambda c: "hi", log=lambda *a: None)
    assert agent.prompt("跑一下") == "完成"
    roles = [m["role"] for m in SessionManager.open(s.path).build_context()]
    assert roles == ["user", "assistant", "tool", "assistant"]


def test_agent_threshold_compaction(tmp_path):
    s = _long_session(tmp_path)
    client, comp = fake_client([text_reply("摘要"), text_reply("答")])
    agent = Agent(client, "m", s, context_window=2000,
                  settings=CompactionSettings(reserve_tokens=1000, keep_recent_tokens=KEEP), log=lambda *a: None)
    agent.prompt("下一步")
    assert any(e["type"] == "compaction" for e in s.entries)
    assert comp.calls[0]["messages"][0]["content"] == prompts.SUMMARIZATION_SYSTEM_PROMPT


def test_small_context_demo_compacts_before_overflow(tmp_path):
    args = parse_args(["--context-window", "20000", "--reserve-tokens", "8000",
                       "--keep-recent-tokens", "4000"])
    s = _long_session(tmp_path, size=6000)
    client, calls = fake_client([text_reply("历史摘要"), text_reply("轮次前缀摘要"), text_reply("继续完成")])
    agent = Agent(client, "m", s, context_window=args.context_window,
                  settings=CompactionSettings(reserve_tokens=args.reserve_tokens,
                                             keep_recent_tokens=args.keep_recent_tokens),
                  log=lambda *a: None)
    assert agent.prompt("继续") == "继续完成"
    assert any(e["type"] == "compaction" for e in s.entries)
    assert calls.calls[0]["max_tokens"] == 6400
    assert "tools" in calls.calls[-1]
    assert len(calls.calls[-1]["messages"]) < 26


def test_agent_overflow_compacts_and_retries(tmp_path):
    s = _long_session(tmp_path)
    err = Exception("This model's maximum context length is 8192 tokens")
    client, comp = fake_client([err, text_reply("摘要"), text_reply("重试成功")])
    agent = Agent(client, "m", s, settings=CompactionSettings(keep_recent_tokens=KEEP), log=lambda *a: None)
    assert agent.prompt("继续") == "重试成功"
    assert len(comp.calls) == 3


def test_manual_compact_custom_instructions(tmp_path):
    s = _long_session(tmp_path)
    client, comp = fake_client([text_reply("摘要")])
    agent = Agent(client, "m", s, settings=CompactionSettings(keep_recent_tokens=KEEP), log=lambda *a: None)
    agent.compact("保留所有报错信息")
    assert comp.calls[0]["messages"][1]["content"].endswith("Additional focus: 保留所有报错信息")


def test_is_context_overflow():
    assert is_context_overflow(Exception("context_length_exceeded"))
    assert not is_context_overflow(Exception("rate limit"))
