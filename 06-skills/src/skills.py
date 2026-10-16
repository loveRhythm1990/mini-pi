"""第 6 章：Skill 系统——按需装载的指令。

一个 Skill 是一个包含 SKILL.md 的目录：frontmatter 声明 name 和 description，
正文写详细指令，可捆绑 scripts/、references/、assets/ 等文件。
启动时只有「名字 + 描述」进入系统提示（渐进式披露）；全文由模型在任务
匹配时用 read 工具自行读取，或由用户 /skill 强制加载。

行为对照 pi 的 skills 机制（docs/skills.md，提交 8bb180b）：
- 发现：目录递归扫描含 SKILL.md 的子目录
- 校验：损坏文件或缺 description 不装载；重名保留先发现者并警告
- 命名：小写字母、数字和单个连字符，≤64 字符；description ≤1024 字符
- disable-model-invocation: true → 不进入自动清单，仍可 /skill 强制
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from registry import CommandResult

SKILL_FILE = "SKILL.md"
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024


class SkillFormatError(ValueError):
    """SKILL.md 结构损坏。"""


@dataclass
class Skill:
    name: str
    description: str
    path: Path                      # SKILL.md 的完整路径；捆绑文件相对其父目录
    allow_model_load: bool = True   # disable-model-invocation: true 时为 False

    def full_text(self) -> str:
        return self.path.read_text(encoding="utf-8")


def parse_skill_file(path: Path) -> tuple[dict, str]:
    """解析 SKILL.md：'---' 围出的 frontmatter（`key: value` 行）+ 正文。

    无 frontmatter 时返回空字段和全文。损坏的结构抛 SkillFormatError；
    未知字段（license、compatibility 等）不报错，原样保留。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    if "---" not in lines[1:]:
        raise SkillFormatError("frontmatter 缺少结束分隔线 '---'")
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise SkillFormatError(f"无效的 frontmatter 行：{line.strip()!r}")
        fields[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1:])
    return fields, body


def rejection_reason(fields: dict) -> str | None:
    """校验规范字段；返回丢弃原因，None 表示通过。"""
    name = fields.get("name")
    if not name:
        return "缺少 name 字段"
    if not NAME_PATTERN.fullmatch(name) or len(name) > NAME_MAX:
        return f"name 不合法（只用小写字母、数字和单个连字符，最长 {NAME_MAX} 字符）：{name!r}"
    description = fields.get("description")
    if not description:
        return "缺少 description 字段（模型靠它判断何时加载这个 Skill）"
    if len(description) > DESCRIPTION_MAX:
        return f"description 超过 {DESCRIPTION_MAX} 字符"
    return None


def load_skills(directories) -> tuple[list[Skill], list[str]]:
    """按顺序扫描目录，返回 (skills, warnings)。

    递归发现含 SKILL.md 的子目录；损坏或缺字段的跳过并警告；
    重名保留先发现者并警告——与 pi 的行为一致。
    """
    skills: list[Skill] = []
    warnings: list[str] = []
    taken: dict[str, Path] = {}
    for root in directories:
        root = Path(root).expanduser()
        if not root.is_dir():
            warnings.append(f"Skill 目录不存在：{root}")
            continue
        for skill_file in sorted(root.rglob(SKILL_FILE)):
            try:
                fields, _body = parse_skill_file(skill_file)
            except SkillFormatError as error:
                warnings.append(f"跳过损坏的 Skill（{skill_file}）：{error}")
                continue
            except OSError as error:
                warnings.append(f"跳过无法读取的 Skill（{skill_file}）：{error}")
                continue
            reason = rejection_reason(fields)
            if reason:
                warnings.append(f"跳过 Skill（{skill_file}）：{reason}")
                continue
            name = fields["name"]
            if name in taken:
                warnings.append(f"重名 Skill「{name}」：保留 {taken[name]}，忽略 {skill_file}")
                continue
            taken[name] = skill_file
            skills.append(Skill(
                name=name,
                description=fields["description"],
                path=skill_file,
                allow_model_load=fields.get("disable-model-invocation", "").strip().lower() != "true",
            ))
    return skills, warnings


def skills_prompt(skills) -> str:
    """生成进入系统提示的 <skills> 清单。

    只有允许自取的 Skill 进入清单；每项一行「名字：描述」加完整指令路径，
    模型按需用 read 读取。清单为空时返回空串，不污染系统提示。
    """
    public = [s for s in skills if s.allow_model_load]
    if not public:
        return ""
    lines = ["<skills>"]
    for s in public:
        lines.append(f"- {s.name}：{s.description}")
        lines.append(f"  完整指令：{s.path}")
    lines.append("</skills>")
    lines.append("任务与某个 Skill 相符时，先用 read 读取它的「完整指令」并按其中步骤执行；"
                 "Skill 内引用的相对路径以其所在目录为基准；与任务无关时忽略此清单。")
    return "\n".join(lines)


def register_skill_command(commands, skills) -> None:
    """/skill <名字> [请求]：把 Skill 全文作为一个 user 消息注入并执行。

    强制加载对 disable-model-invocation 的 Skill 同样有效——这正是该标志的全部含义：
    不进自动清单，但仍保留手动入口。与内置 /compact 命令同款先例：命令处理器
    拿得到 agent，直接调用 agent.prompt() 完成一轮。
    """
    by_name = {s.name: s for s in skills}

    def handler(args, agent):
        name, _, request = args.strip().partition(" ")
        skill = by_name.get(name)
        if skill is None:
            available = "、".join(sorted(by_name)) or "（当前没有可用的 Skill）"
            return CommandResult(f"未知 Skill：{name or '（空）'}。可用：{available}", error=True)
        prompt = (f"{skill.full_text()}\n\n"
                  f"（以上是 Skill「{skill.name}」的完整指令，按它处理下面的请求。）\n\n"
                  f"{request.strip() or '按该 Skill 执行任务。'}")
        agent.prompt(prompt)
        return CommandResult("")

    commands.register("skill", "强制加载某个 Skill 再执行：/skill <名字> [请求]", handler)
