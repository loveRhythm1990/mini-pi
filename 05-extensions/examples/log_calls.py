"""记录工具名称与结果长度，避免把完整参数和文件内容刷满终端。"""


def setup(api):
    def before(event, agent):
        api.report(f"[扩展日志] 即将调用 {event['name']}")

    def after(event, agent):
        api.report(f"[扩展日志] {event['name']} 返回 {len(event['content'])} 字符")

    api.on("before_tool_call", before)
    api.on("after_tool_call", after)
