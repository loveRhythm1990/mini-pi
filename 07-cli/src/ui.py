"""终端渲染与输入；Agent 核心只发事件，不依赖 Rich。"""

from rich.console import Console


class Renderer:
    def __init__(self, *, interactive=False, stream=False, output=None, errors=None):
        self.interactive = interactive
        self.stream = stream
        self.output = Console(file=output, highlight=False, markup=False, soft_wrap=True)
        self.errors = Console(file=errors, stderr=errors is None, highlight=False,
                              markup=False, soft_wrap=True)
        self.partial = False

    def note(self, text):
        self.finish_partial()
        self.errors.print(text)

    def finish_partial(self):
        if self.partial:
            self.output.print()
            self.partial = False

    def event(self, event):
        kind = event["type"]
        if kind == "text_delta" and self.stream:
            self.output.print(event["text"], end="")
            self.partial = True
        elif kind == "message_end":
            if event["streamed"]:
                self.finish_partial()
            elif self.interactive and event["content"]:
                self.output.print(event["content"])
        elif kind == "tool_start":
            self.note(f"🔧 {event['name']}")
        elif kind == "model_start" and self.interactive:
            self.note(f"🤖 模型调用，第 {event['step']} 步")
        elif kind == "tool_end" and self.interactive:
            preview = event["content"][:240]
            suffix = " …" if len(event["content"]) > 240 else ""
            self.note(preview + suffix)
        elif kind == "compaction":
            self.note(f"📦 压缩：{event['tokensBefore']} → {event['tokensAfter']} tokens")
        elif kind == "notice":
            self.note(event["content"])


def make_reader(history_path, commands, plain=False):
    if plain:
        return lambda: input("\nmini-pi> ")
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory

    editor = PromptSession(history=FileHistory(str(history_path)),
                           completer=WordCompleter([line.split()[0] for line in commands.help().splitlines()]))
    return lambda: editor.prompt("mini-pi> ")
