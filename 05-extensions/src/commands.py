"""内置命令与扩展命令走同一个注册表。"""

from previous import tokens, sessions
from registry import CommandResult


def register_commands(commands):
    commands.register("help", "显示命令", lambda args, agent: CommandResult(commands.help()))
    commands.register("quit", "退出", lambda args, agent: CommandResult(exit=True))
    commands.register("tree", "显示会话树", lambda args, agent: CommandResult(agent.session.render_tree()))
    commands.register("tokens", "显示估算 token", lambda args, agent: CommandResult(
        f"约 {tokens.estimate_messages_tokens(agent.context_records())} tokens"))

    def compact(args, agent):
        result = agent.compact(args or None)
        return CommandResult("没有生成新的压缩检查点" if result is None else
                             f"已压缩：{result['tokensBefore']} → {result['tokensAfter']} tokens")

    def branch(args, agent):
        if not args:
            return CommandResult("用法：/branch <完整 entry id>", error=True)
        if args not in agent.session.by_id:
            return CommandResult(f"Entry {args} not found", error=True)
        # 不能切到工具批次的中间，否则后继请求会缺失对应的结果。
        pending = set()
        for entry in agent.session.get_branch(args):
            for message in sessions.entry_to_messages(entry):
                if message.get("tool_calls"):
                    pending.update(tc["id"] for tc in message["tool_calls"])
                elif message["role"] == "tool":
                    pending.discard(message["tool_call_id"])
        if pending:
            return CommandResult("该位置的工具批次未完成，请选择所有工具结果之后的位置", error=True)
        agent.session.branch(args)
        return CommandResult("已切换分支；下一条消息将创建新分支，旧文件内容不变")

    commands.register("compact", "压缩上下文，可附加关注点", compact)
    commands.register("branch", "从指定 entry 开新分支", branch)
