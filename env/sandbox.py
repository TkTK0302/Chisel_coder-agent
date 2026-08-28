"""执行环境沙盒：Docker 优先，宿主机兜底。

设计来源（借鉴 OpenHands runtime 思路，自写最小版）：
  - 用 docker SDK 启动一个**常驻容器**（command="sleep infinity"），把工作目录
    bind mount 到容器 /workspace，用 exec_run 执行命令 —— 等价于 OpenHands 的
    "持久化容器"模型，省掉整套 action-execution-server。
  - exec_run 无原生超时，用线程 + join 包裹。
  - 拉镜像失败 / daemon 不可达 / 挂载探针失败 → 自动降级 HostBackend。

安全设计说明：文件的读写由宿主侧结构化工具（write/edit）完成，容器内只执行命令，
二者共享同一份 bind mount 的工作目录 —— 任意 shell 命令（安装、删除、编译）在
容器里跑，不在宿主机上留下副作用。
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

SANDBOX_IMAGE = os.environ.get("CHISEL_SANDBOX_IMAGE", "python:3.12-slim")
CHISEL_PREFIX = "chisel-sandbox-"


class HostBackend:
    """宿主机一次性执行（subprocess）。"""

    name = "host"

    def __init__(self, workspace: str):
        self.workspace = workspace

    def available(self) -> bool:
        return True

    def run(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=workdir or self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            parts = []
            if proc.stdout.strip():
                parts.append(proc.stdout.rstrip())
            if proc.stderr.strip():
                parts.append("[stderr] " + proc.stderr.rstrip())
            parts.append(f"[exit code {proc.returncode}]")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return f"命令超时（{timeout}s），已终止。"


class DockerBackend:
    """Docker 常驻容器 + exec_run。拉镜像/启动/探针任一失败则抛异常（由上层降级）。"""

    name = "docker"

    def __init__(self, workspace: str, image: str = SANDBOX_IMAGE):
        import docker  # 延迟导入：只有真正走 docker 模式才需要

        self.client = docker.from_env()
        self.image = image
        self.workspace = workspace
        self.container = None

    # --- 生命周期 ----------------------------------------------------------

    def available(self) -> bool:
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def _ensure_image(self) -> None:
        try:
            self.client.images.get(self.image)
        except Exception:
            # 拉镜像失败会抛异常，由外层捕获后降级宿主
            self.client.images.pull(self.image, timeout=300)

    def _ensure_container(self):
        """启动常驻容器并验证双向挂载。返回 container。"""
        if self.container is not None:
            try:
                self.container.reload()
                if self.container.status in ("running", "created"):
                    return self.container
            except Exception:
                pass
        # 清理同名旧容器
        for c in self.client.containers.list(all=True, filters={"name": CHISEL_PREFIX}):
            try:
                c.remove(force=True)
            except Exception:
                pass
        ws = str(Path(self.workspace).resolve())
        self.container = self.client.containers.run(
            self.image,
            command="sleep infinity",
            detach=True,
            tty=True,
            stdin_open=True,
            working_dir="/workspace",
            volumes={ws: {"bind": "/workspace", "mode": "rw"}},
            name=CHISEL_PREFIX + "main",
        )
        # 探针：验证双向挂载可写（Windows Docker 挂载失败时立即发现）
        code, out, err = self._exec("echo ok > /workspace/.chisel_probe && cat /workspace/.chisel_probe", timeout=30)
        if code != 0 or out.strip() != "ok":
            raise RuntimeError(f"容器挂载探针失败（{code} {err[:80]}），降级宿主")
        # 清理探针文件
        self._exec("rm -f /workspace/.chisel_probe", timeout=30)
        return self.container

    # --- 执行 --------------------------------------------------------------

    def _exec(self, command: str, timeout: int = 120):
        """在容器内执行命令，返回 (exit_code, stdout, stderr)。超时返回 (124, "", 提示)。"""
        container = self._ensure_container()
        result: dict = {}

        def _run():
            try:
                result["res"] = container.exec_run(
                    ["/bin/sh", "-c", command],
                    workdir="/workspace",
                    demux=True,
                    # Python 默认块缓冲，重定向到文件后输出会滞后；强制实时 flush
                    environment={
                        "PYTHONUNBUFFERED": "1",
                        "PYTHONIOENCODING": "utf-8",
                        "LANG": "C.UTF-8",
                    },
                )
            except Exception as e:  # 容器异常（如被删）时抛给外层
                result["exc"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self._recycle()  # 超时：丢弃该容器，下次重建，避免进程泄漏
            return 124, "", f"命令超时（{timeout}s），已终止。"
        if "exc" in result:
            raise result["exc"]
        exit_code, output = result["res"]
        if isinstance(output, tuple):  # demux=True
            out_b, err_b = output
            out = (out_b or b"").decode("utf-8", errors="replace")
            err = (err_b or b"").decode("utf-8", errors="replace")
        else:
            out = (output or b"").decode("utf-8", errors="replace")
            err = ""
        return exit_code, out, err

    def _recycle(self) -> None:
        try:
            self.container.remove(force=True)
        except Exception:
            pass
        self.container = None

    def run(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        code, out, err = self._exec(command, timeout)
        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("[stderr] " + err.rstrip())
        parts.append(f"[exit code {code}]")
        return "\n".join(parts)


class Sandbox:
    """门面：按 mode 选择后端，把命令丢进去执行，结果统一为字符串。"""

    def __init__(self, workspace: str, mode: str = "auto", image: str = SANDBOX_IMAGE):
        self.workspace = workspace
        self.mode = mode
        self.backend: HostBackend | DockerBackend
        self.image = image
        self._init_backend()

    def _init_backend(self):
        if self.mode == "host":
            self.backend = HostBackend(self.workspace)
            return
        try:
            backend = DockerBackend(self.workspace, self.image)
            if not backend.available():
                raise RuntimeError("Docker daemon 不可达")
            backend._ensure_image()
            backend._ensure_container()
            self.backend = backend
        except Exception as e:
            if self.mode == "docker":
                raise RuntimeError(f"Docker 沙盒不可用：{e}") from e
            print(f"  ⚠️ Docker 沙盒不可用（{e}），自动降级宿主机执行。")
            self.backend = HostBackend(self.workspace)

    def run(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        result = self.backend.run(command, workdir, timeout)
        return f"[{self.backend.name}] " + result

    def run_raw(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        """不带后端标记的原始输出（供 terminal 解析 pid 等）。"""
        return self.backend.run(command, workdir, timeout)

    def status(self) -> str:
        return f"[{self.backend.name}]"

    @property
    def name(self) -> str:
        """当前生效的后端名（host / docker），供 terminal 等判断走哪个分支。"""
        return self.backend.name
