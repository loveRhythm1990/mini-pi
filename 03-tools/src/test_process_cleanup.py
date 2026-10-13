"""POSIX 集成测试：中断 Python 调用者之后，shell 的子进程也退出。"""

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 简化实现不保证清理整个进程树")
def test_keyboard_interrupt_terminates_shell_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    command = f'sleep 60 & child=$!; printf "%s" "$child" > {shlex.quote(str(pid_file))}; wait'
    script = "import sys; sys.path.insert(0, sys.argv[1]); from tools import tool_bash; tool_bash(sys.argv[2])"
    process = subprocess.Popen([sys.executable, "-c", script, str(Path(__file__).parent), command],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() or not pid_file.read_text():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("未启动 shell 子进程")
            time.sleep(0.02)
        child_pid = int(pid_file.read_text())
        process.send_signal(signal.SIGINT)
        _, errors = process.communicate(timeout=5)
        assert process.returncode != 0 and "KeyboardInterrupt" in errors
        deadline = time.monotonic() + 3
        while True:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                raise AssertionError("中断后 shell 子进程仍在运行")
            time.sleep(0.02)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
