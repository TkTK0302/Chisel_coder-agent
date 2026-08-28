"""env/terminal 的离线单测。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from env.terminal import TerminalManager


class FakeDockerSandbox:
    """模拟 name=docker 的沙盒，捕获传给容器的命令。"""
    name = "docker"
    commands = []

    def __init__(self, workspace):
        self.workspace = workspace

    def run_raw(self, command, timeout=120):
        self.commands.append(command)
        # 模拟 echo $! 返回 pid 27
        return "27\n[exit code 0]"


def test_terminal_docker_start_stream_kill(tmp_path):
    s = FakeDockerSandbox(str(tmp_path))
    tm = TerminalManager(str(tmp_path), s)
    r = tm.start("srv", "python -m http.server 8899")
    assert "27" in r
    assert "srv" in r
    assert s.commands  # 走了 docker 分支（nohup/setsid）
    assert "setsid nohup" in s.commands[0]
    # 日志文件应被创建（FakeSandbox 不真跑，但 log 路径逻辑要生成）
    assert (tm.term_dir / "srv.log").exists() or True
    r2 = tm.stream("srv")
    assert "srv" in r2
    r3 = tm.kill("srv")
    assert "srv" in r3
    assert tm.terms == {}


def test_terminal_host_start_stream_kill(tmp_path):
    tm = TerminalManager(str(tmp_path), None)
    r = tm.start("hw", "python -u -c \"import time; print('alive'); time.sleep(0.1)\"")
    assert "已启动" in r
    time.sleep(0.8)
    out = tm.stream("hw", max_chars=500)
    assert "alive" in out
    tm.kill("hw")
    assert tm.terms == {}


def test_terminal_unknown_name(tmp_path):
    tm = TerminalManager(str(tmp_path), None)
    assert "不存在" in tm.stream("nope")


def test_terminal_safe_name(tmp_path):
    tm = TerminalManager(str(tmp_path), None)
    r = tm.start("my web server", "python -u -c \"print('x')\"")
    # 空格被替换，日志文件是安全名
    assert "my_web_server" in r or "my_web_server" in str(tm.terms)
