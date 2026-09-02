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
            return f"命令超时（{timeout}s），已终止。\n[exit code 124]"

    def start_background(self, name: str, command: str, log_path: str) -> str:
        """宿主机后台启动进程，返回 pid。"""
        import os as _os
        f = open(log_path, "wb")
        env = {**_os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=self.workspace,
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
        )
        return str(proc.pid)

    def kill_background(self, pid: str) -> None:
        """宿主机终止后台进程。"""
        import subprocess as _sp
        try:
            _sp.run(["taskkill", "/T", "/F", "/PID", pid],
                    capture_output=True, timeout=30)
        except Exception:
            pass


class DockerBackend:
    """Docker 常驻容器 + exec_run。拉镜像/启动/探针任一失败则抛异常（由上层降级）。"""

    name = "docker"

    def __init__(self, workspace: str, image: str = SANDBOX_IMAGE):
        import docker  # 延迟导入：只有真正走 docker 模式才需要

        self.client = docker.from_env()
        self.image = image
        self.workspace = workspace
        self.container = None
        self._on_recycle_callbacks: list = []  # Q3: 容器回收时通知依赖方

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
        # Q6: 双向探针验证（Windows Docker 挂载可能单向不可见）
        probe = Path(self.workspace) / ".chisel_probe"
        try:
            # 方向 1：宿主机 → 容器内
            probe.write_text("host_to_container", encoding="utf-8")
            code, out, _ = self._exec_raw("cat /workspace/.chisel_probe", timeout=10)
            if out.strip() != "host_to_container":
                raise RuntimeError(f"宿主机→容器挂载失败：写入 'host_to_container'，读到 '{out.strip()}'")
            # 方向 2：容器内 → 宿主机
            self._exec_raw("echo 'container_to_host' > /workspace/.chisel_probe", timeout=10)
            if probe.read_text(encoding="utf-8").strip() != "container_to_host":
                raise RuntimeError("容器→宿主机挂载失败：容器写入后宿主机无法读取")
        finally:
            probe.unlink(missing_ok=True)
        return self.container

    # --- 执行 --------------------------------------------------------------

    def _exec_raw(self, command: str, timeout: int = 30):
        """内部探针专用：不触发 _ensure_container，避免递归。直接 exec_run。"""
        container = self.container
        result: dict = {}

        def _run():
            try:
                result["res"] = container.exec_run(
                    ["/bin/sh", "-c", command],
                    workdir="/workspace",
                    demux=True,
                )
            except Exception as e:
                result["exc"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return 124, "", "超时"
        if "exc" in result:
            raise result["exc"]
        exit_code, output = result["res"]
        if isinstance(output, tuple):
            out = (output[0] or b"").decode("utf-8", errors="replace")
            err = (output[1] or b"").decode("utf-8", errors="replace")
        else:
            out = (output or b"").decode("utf-8", errors="replace")
            err = ""
        return exit_code, out, err

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
        # Q3: 通知依赖方（TerminalManager 等）容器即将销毁
        for cb in self._on_recycle_callbacks:
            try:
                cb()
            except Exception:
                pass
        try:
            self.container.remove(force=True)
        except Exception:
            pass
        self.container = None

    def on_recycle(self, callback) -> None:
        """Q3: 注册容器回收回调。TerminalManager 等依赖方用于清理状态。"""
        self._on_recycle_callbacks.append(callback)

    def run(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        code, out, err = self._exec(command, timeout)
        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("[stderr] " + err.rstrip())
        parts.append(f"[exit code {code}]")
        return "\n".join(parts)

    # --- Q10: 进程管理方法 -------------------------------------------------

    def start_background(self, name: str, command: str, log_path: str) -> str:
        """容器内后台启动进程，返回 pid。

        用 exec 确保 sh 被命令进程替换，pid 指向实际命令而非 shell 包装。
        setsid 脱离 exec 的进程组，避免容器 exec 退出时清理后台进程。
        """
        log_inside = f"/workspace/.chisel/terms/{name}.log"
        inner = (
            f"mkdir -p /workspace/.chisel/terms && "
            f"setsid nohup sh -c 'exec {command}' > {log_inside} 2>&1 < /dev/null & echo $!"
        )
        out = self.run_raw(inner, timeout=30)
        body = out.split("[exit code")[0].strip()
        lines = [ln for ln in body.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""

    def kill_background(self, pid: str, name: str = "") -> None:
        """容器内终止后台进程。"""
        import shlex as _shlex
        self.run_raw(
            f"kill -9 {_shlex.quote(pid)} 2>/dev/null || "
            f"pkill -9 -f {_shlex.quote(name)} 2>/dev/null; true",
            timeout=30,
        )

    def run_raw(self, command: str, workdir: str | None = None, timeout: int = 120) -> str:
        """不带后端标记的原始输出（供 terminal 解析 pid 等）。"""
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

    # --- Q10: 进程管理（门面封装后端差异，TerminalManager 不再判断后端类型）---

    def start_background(self, name: str, command: str, log_path: str) -> str:
        """启动后台进程，返回进程标识符（pid）。"""
        return self.backend.start_background(name, command, log_path)

    def kill_background(self, pid: str, name: str = "") -> None:
        """终止后台进程。"""
        self.backend.kill_background(pid, name)

    # --- Q3: 容器回收回调（TerminalManager 注册以清理失效状态）---

    def on_recycle(self, callback) -> None:
        """注册容器回收回调。仅 Docker 后端支持，Host 后端忽略。"""
        if hasattr(self.backend, "on_recycle"):
            self.backend.on_recycle(callback)
