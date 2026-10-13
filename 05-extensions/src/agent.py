"""第 5 章：把 Provider、文件工具、会话和扩展接入同一循环。"""

from dataclasses import dataclass

from extensions import CompactDecision, HookError, ResultDecision, ToolDecision
from previous import CompactionSettings, compaction, tokens
from providers import SummaryClient, to_message, to_record

SYSTEM_PROMPT = "你是编程助手。使用 read 查看文件，write 创建文件，edit 精确修改，bash 运行命令。"


@dataclass
class TurnResult:
    content: str
    status: str  # completed / max_steps
    steps: int


class Agent:
    def __init__(self, provider, session, extensions, *, context_window=128000,
                 settings=None, notify=None):
        self.provider = provider
        self.session = session
        self.extensions = extensions
        self.context_window = context_window
        self.settings = settings or CompactionSettings()
        self.notify = notify or (lambda event: None)
        self.repair_pending_results()

    def context_records(self):
        return [{"role": "system", "content": SYSTEM_PROMPT}] + self.session.build_context()

    def repair_pending_results(self, reason="之前的工具批次未完成；未自动重放，请先检查实际状态"):
        """崩溃或中断后补齐未返回的结果，绝不重新执行有副作用的工具。"""
        pending = {}
        for message in self.session.build_context():
            if message.get("tool_calls"):
                if pending:
                    raise ValueError("会话中间存在未配对的工具调用，请从完整轮次重新分支")
                pending = {tc["id"]: tc for tc in message["tool_calls"]}
            elif message["role"] == "tool":
                if message["tool_call_id"] not in pending:
                    raise ValueError("会话中存在没有对应调用的工具结果")
                pending.pop(message["tool_call_id"], None)
            elif pending:
                raise ValueError("会话中间存在未配对的工具调用，请从完整轮次重新分支")
        for call_id in pending:
            self.session.append_message({"role": "tool", "tool_call_id": call_id,
                                         "content": f"错误: {reason}"})

    def compact(self, instructions=None):
        try:
            for decision in self.extensions.results("before_compact", {"instructions": instructions}, self, strict=True):
                if decision is not None and not isinstance(decision, CompactDecision):
                    raise HookError("before_compact 必须返回 CompactDecision 或 None")
                if decision and decision.cancel:
                    self.notify({"type": "notice", "content": f"压缩已取消：{decision.reason}"})
                    return None
        except HookError as error:
            self.notify({"type": "notice", "content": f"压缩未执行：{error}"})
            return None
        result = compaction.compact(self.session, SummaryClient(self.provider),
                                    self.provider.model, self.settings, instructions)
        if result:
            self.notify({"type": "compaction", **result})
            self.extensions.emit("after_compact", result, self)
        return result

    def maybe_compact(self):
        size = tokens.estimate_messages_tokens(self.context_records())
        if compaction.should_compact(size, self.context_window, self.settings):
            self.compact()

    def _completion(self, stream):
        response = self.provider.create_completion(
            [to_message(m) for m in self.context_records()],
            self.extensions.tools.definitions(), stream=stream)
        if not stream:
            return response
        final = None
        try:
            for event in response:
                if event["type"] == "text_delta":
                    self.notify(event)
                elif event["type"] == "done":
                    final = event["message"]
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if final is None:
            raise RuntimeError("流式响应未返回 done；未保存或执行不完整的消息")
        return final

    def _call_llm(self, stream):
        try:
            return self._completion(stream)
        except Exception as error:
            if not compaction.is_context_overflow(error) or self.compact() is None:
                raise
            self.notify({"type": "notice", "content": "上下文超限，压缩后重试一次"})
            return self._completion(stream)

    def _execute(self, call):
        event = {"id": call.id, "name": call.name, "arguments": call.arguments}
        try:
            for decision in self.extensions.results("before_tool_call", event, self, strict=True):
                if decision is not None and not isinstance(decision, ToolDecision):
                    raise HookError("before_tool_call 必须返回 ToolDecision 或 None")
                if decision and decision.block:
                    return f"错误: 工具调用被扩展拦截: {decision.reason}"
                if decision and decision.arguments is not None:
                    event["arguments"] = decision.arguments
        except HookError as error:
            return f"错误: 工具未执行: {error}"
        self.notify({"type": "tool_start", **event})
        content = self.extensions.tools.execute(call.name, event["arguments"])
        event["content"] = content
        for decision in self.extensions.results("after_tool_call", event, self):
            if isinstance(decision, ResultDecision) and isinstance(decision.content, str):
                content = decision.content
                event["content"] = content
            elif decision is not None:
                self.extensions.report("扩展错误 [after_tool_call]: 必须返回 ResultDecision 或 None")
        return content

    def prompt(self, text, *, max_steps=20, stream=False):
        if max_steps <= 0:
            raise ValueError("max_steps 必须为正数")
        self.session.append_message({"role": "user", "content": text})
        self.extensions.emit("turn_start", {"prompt": text}, self)
        status, final, step = "error", "", 0
        try:
            for step in range(1, max_steps + 1):
                self.maybe_compact()
                self.notify({"type": "model_start", "step": step})
                message = self._call_llm(stream)
                ids = [call.id for call in message.tool_calls or []]
                if any(not call_id for call_id in ids) or len(ids) != len(set(ids)):
                    raise RuntimeError("模型返回空或重复的工具调用 id，未保存或执行该消息")
                self.session.append_message(to_record(message))
                final = message.content or ""
                self.notify({"type": "message_end", "content": final, "streamed": stream})
                if message.stop_reason in ("length", "max_tokens"):
                    self.repair_pending_results("模型输出被截断，工具未执行")
                    raise RuntimeError("模型输出被截断，请缩小任务或调整模型输出预算")
                if not message.tool_calls:
                    status = "completed"
                    return TurnResult(final, status, step)
                for call in message.tool_calls:
                    content = self._execute(call)
                    self.session.append_message({"role": "tool", "tool_call_id": call.id, "content": content})
                    self.notify({"type": "tool_end", "id": call.id, "name": call.name, "content": content})
            status = "max_steps"
            self.notify({"type": "notice", "content": f"达到 {max_steps} 步上限，任务尚未确认完成"})
            return TurnResult(final, status, step)
        except BaseException:
            self.repair_pending_results("本轮中断；执行中的工具可能已有副作用，请先检查实际状态")
            raise
        finally:
            self.extensions.emit("turn_end", {"status": status, "steps": step}, self)
