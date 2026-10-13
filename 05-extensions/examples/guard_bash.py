"""只演示拦截钩子：字符串匹配无法构成可靠的 shell 安全策略。"""

from extensions import ToolDecision


def setup(api):
    def before(event, agent):
        if event["name"] == "bash" and "rm " in event["arguments"].get("command", ""):
            return ToolDecision(block=True, reason="示例扩展禁止包含 'rm ' 的命令")

    api.on("before_tool_call", before)
