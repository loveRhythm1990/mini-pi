"""第 6 章测试：发现、校验、清单生成、两条加载路径的端到端，全部离线。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap  # noqa: F401,E402  # 拿到第 5 章 src
from agent import Agent  # noqa: E402
from commands import register_commands  # noqa: E402
from extensions import ExtensionAPI  # noqa: E402
from previous import Message, SessionManager, ToolCall  # noqa: E402
from registry import CommandRegistry, builtin_tools  # noqa: E402
from skills import (Skill, SkillFormatError, load_skills,  # noqa: E402
                    parse_skill_file, register_skill_command, skills_prompt)


def write_skill(root, name="demo-skill", description="演示用 Skill",
                body="# 演练指令\n按步骤执行。", extra_fields=()):
    directory = root / name
    directory.mkdir(parents=True)
    fields = [f"name: {name}", f"description: {description}", *extra_fields]
    front = "---\n" + "\n".join(fields) + "\n---\n"
    (directory / "SKILL.md").write_text(front + body + "\n", encoding="utf-8")
    return directory / "SKILL.md"


class ScriptedProvider:
    model = "test-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create_completion(self, messages, tools, stream=False, max_tokens=None):
        self.calls.append({"messages": messages, "tools": tools})
        reply = self.replies.pop(0)
        return reply

    def to_native_messages(self, messages):
        return []

    def from_native_message(self, message):
        return message


def build_agent(tmp_path, replies, skills, notify=None):
    provider = ScriptedProvider(replies)
    session = SessionManager.create(str(tmp_path / "sessions"))
    commands = CommandRegistry()
    register_commands(commands)
    register_skill_command(commands, skills)
    api = ExtensionAPI(builtin_tools(), commands, report=lambda text: None)
    agent = Agent(provider, session, api, notify=notify or (lambda event: None),
                  system_suffix=skills_prompt(skills))
    return agent, provider


# ---------- 解析与校验 ----------

def test_parse_frontmatter_and_body(tmp_path):
    path = write_skill(tmp_path, extra_fields=("disable-model-invocation: true",))
    fields, body = parse_skill_file(path)
    assert fields["name"] == "demo-skill"
    assert fields["description"] == "演示用 Skill"
    assert fields["disable-model-invocation"] == "true"
    assert body.startswith("# 演练指令")


def test_parse_without_frontmatter(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("只有正文，没有 frontmatter。\n", encoding="utf-8")
    fields, body = parse_skill_file(path)
    assert fields == {}
    assert "只有正文" in body


def test_parse_unterminated_frontmatter(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: broken\n正文（缺少结束分隔线）\n", encoding="utf-8")
    with pytest.raises(SkillFormatError):
        parse_skill_file(path)


def test_load_discovers_recursively(tmp_path):
    write_skill(tmp_path / "group", name="first-skill")
    write_skill(tmp_path / "group" / "nested", name="second-skill")
    skills, warnings = load_skills([tmp_path])
    assert [s.name for s in skills] == ["first-skill", "second-skill"]
    assert warnings == []


def test_invalid_skills_are_skipped_with_warnings(tmp_path):
    write_skill(tmp_path, name="OK-skill")            # 大写，非法
    write_skill(tmp_path / "second", description="")   # 缺 description
    write_skill(tmp_path / "third", extra_fields=("junk-line",))  # 无冒号的行 → 损坏
    skills, warnings = load_skills([tmp_path])
    assert skills == []
    assert len(warnings) == 3


def test_name_length_limit(tmp_path):
    write_skill(tmp_path, name="a" * 65)
    skills, warnings = load_skills([tmp_path])
    assert skills == []
    assert "64" in warnings[0]


def test_collision_keeps_first_discovered(tmp_path):
    first = write_skill(tmp_path / "a", name="same-skill", description="第一个")
    write_skill(tmp_path / "b", name="same-skill", description="第二个")
    skills, warnings = load_skills([tmp_path])
    assert len(skills) == 1
    assert skills[0].path == first
    assert any("重名" in warning for warning in warnings)


def test_unknown_fields_are_tolerated(tmp_path):
    write_skill(tmp_path, extra_fields=("license: MIT", "metadata: 无所谓"))
    skills, warnings = load_skills([tmp_path])
    assert len(skills) == 1
    assert warnings == []


def test_missing_directory_warns(tmp_path):
    skills, warnings = load_skills([tmp_path / "nope"])
    assert skills == []
    assert any("不存在" in warning for warning in warnings)


# ---------- 清单与系统提示 ----------

def test_skills_prompt_lists_only_auto_skills(tmp_path):
    auto = write_skill(tmp_path, name="auto-skill", description="自动可见")
    hidden = write_skill(tmp_path / "hidden", name="hidden-skill", description="只能强制",
                         extra_fields=("disable-model-invocation: true",))
    skills, _ = load_skills([tmp_path])
    block = skills_prompt(skills)
    assert "auto-skill" in block
    assert "hidden-skill" not in block
    assert str(auto) in block          # 完整指令路径在清单里，模型用它 read
    assert "<skills>" in block
    assert "read" in block             # 自取规则


def test_skills_prompt_empty_when_nothing_public(tmp_path):
    write_skill(tmp_path, name="locked-skill",
                extra_fields=("disable-model-invocation: true",))
    skills, _ = load_skills([tmp_path])
    assert skills_prompt(skills) == ""


# ---------- 端到端：两条加载路径 ----------

def test_model_loads_skill_via_read_tool(tmp_path):
    """模型自取：系统提示带清单 → 模型调 read 读 SKILL.md → 真实工具返回全文。"""
    skill_path = write_skill(tmp_path / "skills", body="# 演练指令\n第一步先看这里。\n")
    skills, warnings = load_skills([tmp_path / "skills"])
    assert warnings == []

    read_call = ToolCall("call-1", "read", {"path": str(skill_path)})
    final = Message("assistant", "已按 Skill 完成检查。", stop_reason="stop")
    agent, provider = build_agent(tmp_path, [
        Message("assistant", "任务匹配 skill。", [read_call], stop_reason="tool_calls"),
        final,
    ], skills)

    result = agent.prompt("对仓库做检查然后收尾")
    assert result.status == "completed"
    assert result.content == "已按 Skill 完成检查。"

    # 第一次请求的系统提示里带着 <skills> 清单和这个 Skill 的路径
    first_messages = provider.calls[0]["messages"]
    assert "<skills>" in first_messages[0].content
    assert "demo-skill" in first_messages[0].content

    # read 工具真实读取了 SKILL.md：工具消息（第二条请求的末条）包含正文
    second_messages = provider.calls[1]["messages"]
    assert "第一步先看这里" in second_messages[-1].content


def test_force_load_via_skill_command(tmp_path):
    """强制加载：/skill 把全文作为 user 消息直接注入，不需要模型自取。"""
    skill_path = write_skill(tmp_path / "skills", body="# 演练指令\n强制加载也可以。\n")
    skills, _ = load_skills([tmp_path / "skills"])

    agent, provider = build_agent(tmp_path, [
        Message("assistant", "收到，按 Skill 执行。", stop_reason="stop"),
    ], skills)

    result, exit_flag = None, None
    from registry import CommandResult  # noqa: E402
    command = agent.extensions.commands.dispatch("/skill demo-skill 总结改动", agent)
    assert isinstance(command, CommandResult) and not command.error

    # 唯一一次请求：user 消息带着 SKILL.md 全文和用户的请求
    messages = provider.calls[0]["messages"]
    user = messages[1]
    assert user.role == "user"
    assert "强制加载也可以" in user.content   # SKILL.md 正文被注入
    assert "总结改动" in user.content          # /skill 后面的参数附在请求里


def test_force_load_works_for_hidden_skill(tmp_path):
    """disable-model-invocation 的 Skill 不进自动清单，但 /skill 仍然可用。"""
    write_skill(tmp_path / "skills", name="private-skill",
                extra_fields=("disable-model-invocation: true",))
    skills, _ = load_skills([tmp_path / "skills"])
    assert skills_prompt(skills) == ""

    agent, provider = build_agent(tmp_path, [
        Message("assistant", "按隐藏 Skill 执行完毕。", stop_reason="stop"),
    ], skills)
    command = agent.extensions.commands.dispatch("/skill private-skill", agent)
    assert not command.error
    # user 消息里注入了隐藏 Skill 的全文（带 frontmatter）
    assert "name: private-skill" in provider.calls[0]["messages"][1].content
    # 最终回复进入会话
    assistant = [m for m in agent.session.build_context() if m.get("role") == "assistant"][-1]
    assert assistant["content"] == "按隐藏 Skill 执行完毕。"


def test_unknown_skill_command_errors(tmp_path):
    skills, _ = load_skills([tmp_path])
    agent, _ = build_agent(tmp_path, [], skills)
    command = agent.extensions.commands.dispatch("/skill nope", agent)
    assert command.error
    assert "未知 Skill" in command.content
