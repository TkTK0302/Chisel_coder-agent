"""env/sandbox 的离线单测（Docker 路径用 mock，不依赖真实 daemon）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from env.sandbox import HostBackend, Sandbox


def test_host_backend_runs_command(tmp_path):
    b = HostBackend(str(tmp_path))
    r = b.run("python -c \"print(6*7)\"")
    assert "42" in r
    assert "[exit code 0]" in r


def test_host_backend_captures_stderr(tmp_path):
    b = HostBackend(str(tmp_path))
    r = b.run("python -c \"import sys; print('oops', file=sys.stderr)\"")
    assert "[stderr]" in r


def test_host_backend_timeout(tmp_path):
    b = HostBackend(str(tmp_path))
    r = b.run("python -c \"import time; time.sleep(5)\"", timeout=1)
    assert "超时" in r


def test_sandbox_host_mode(tmp_path):
    s = Sandbox(str(tmp_path), mode="host")
    assert s.name == "host"
    r = s.run("echo hi")
    assert r.startswith("[host] ")
    assert "hi" in r


def test_sandbox_auto_degrades_to_host_when_docker_down(tmp_path, monkeypatch):
    """Docker daemon 不可达/初始化失败时自动降级宿主。"""
    monkeypatch.setattr(
        "env.sandbox.DockerBackend.available", lambda self: False
    )
    s = Sandbox(str(tmp_path), mode="auto")
    assert s.name == "host"


def test_sandbox_force_docker_raises_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "env.sandbox.DockerBackend.available", lambda self: False
    )
    with pytest.raises(RuntimeError, match="Docker 沙盒不可用"):
        Sandbox(str(tmp_path), mode="docker")


def test_sandbox_auto_uses_docker_when_available(tmp_path, monkeypatch):
    """Docker 可用且探针通过时走 docker。"""
    class FakeDocker:
        name = "docker"

        def __init__(self, workspace, image=None):
            self.workspace = workspace

        def available(self):
            return True

        def _ensure_image(self):
            pass

        def _ensure_container(self):
            pass

        def run(self, command, workdir=None, timeout=120):
            return "ok"

    monkeypatch.setattr("env.sandbox.DockerBackend", FakeDocker)
    s = Sandbox(str(tmp_path), mode="auto")
    assert s.name == "docker"
    assert "ok" in s.run("echo x")
