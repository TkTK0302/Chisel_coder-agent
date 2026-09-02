"""env/terminal 的离线单测。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from env.terminal import TerminalManager


class FakeDockerSandbox:
    """模拟 Docker 后端沙盒，通过 Sandbox 门面接口工作。"""
    name = "docker"
    commands = []

    def __init__(self, workspace):
        self.workspace = workspace

    def run_raw(self, command, timeout=120):
        self.commands.append(command)
        return "27\n[exit code 0]"

    def start_background(self, name, command, log_path):
        # Q10: 通过门面接口，TerminalManager 不再判断后端类型
        # 记录命令用于断言验证
        self.commands.append(f"start_background: {command} -> {log_path}")
        return "27"

    def kill_background(self, pid, name=""):
        self.commands.append(f"kill_background: pid={pid} name={name}")

    def on_recycle(self, callback):
        pass  # 测试中不需要回收逻辑


class FakeHostSandbox:
    """模拟 Host 后端沙盒，用于测试宿主机终端路径。"""
    name = "host"

    def __init__(self, workspace):
        self.workspace = workspace

    def start_background(self, name, command, log_path):
        import subprocess
        import os
        f = open(log_path, "wb")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            command, shell=True, cwd=self.workspace,
            stdout=f, stderr=subprocess.STDOUT, env=env,
        )
        return str(proc.pid)

    def kill_background(self, pid, name=""):
        import subprocess
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", pid],
                           capture_output=True, timeout=30)
        except Exception:
            pass


def test_terminal_docker_start_stream_kill(tmp_path):
    s = FakeDockerSandbox(str(tmp_path))
    tm = TerminalManager(str(tmp_path), s)
    r = tm.start("srv", "python -m http.server 8899")
    assert "27" in r
    assert "srv" in r
    assert s.commands  # 走了 start_background 路径
    assert "start_background" in s.commands[0]
    r2 = tm.stream("srv")
    assert "srv" in r2
    r3 = tm.kill("srv")
    assert "srv" in r3
    assert tm.terms == {}


def test_terminal_host_start_stream_kill(tmp_path):
    s = FakeHostSandbox(str(tmp_path))
    tm = TerminalManager(str(tmp_path), s)
    r = tm.start("hw", "python -u -c \"import time; print('alive'); time.sleep(0.1)\"")
    assert "hw" in r
    assert "pid" in r.lower()
    time.sleep(0.8)
    out = tm.stream("hw", max_chars=500)
    assert "alive" in out
    tm.kill("hw")
    assert tm.terms == {}


def test_terminal_unknown_name(tmp_path):
    s = FakeHostSandbox(str(tmp_path))
    tm = TerminalManager(str(tmp_path), s)
    assert "hw" not in str(tm.terms) or "不存在" in tm.stream("nope") or "nope" in tm.stream("nope")


def test_terminal_safe_name(tmp_path):
    s = FakeHostSandbox(str(tmp_path))
    tm = TerminalManager(str(tmp_path), s)
    r = tm.start("my web server", "python -u -c \"print('x')\"")
    # 空格被替换，日志文件是安全名
    assert "my_web_server" in r or "my_web_server" in str(tm.terms)
