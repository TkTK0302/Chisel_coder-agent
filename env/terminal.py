"""交互式终端：启动/流式查看/终止长驻进程（如 web server）。

两种后端统一到"日志文件 + 游标"模型：
  - 宿主：Popen(command, shell=True) 把 stdout 重定向到 .chisel/terms/<name>.log。
  - Docker：容器内 nohup 后台运行，输出写到同一份日志（工作目录是 bind mount，
    宿主可直接读），pid 通过 echo $! 拿回。
stream 用字节游标返回"自上次读取后新增"的输出。
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from tools import register_tool

TERMINAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "启动/查看/终止一个长期运行或交互式进程（如 web server、长编译）。"
                       "短命令请用 bash。每个终端用 name 区分，可同时运行多个。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stream", "kill"]},
                "name": {"type": "string", "description": "终端名称，同一 name 复用"},
                "command": {"type": "string", "description": "action=start 时要启动的命令"},
                "max_chars": {"type": "integer", "description": "action=stream 时返回的最大字符数，默认 4000"},
            },
            "required": ["action", "name"],
        },
    },
}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))


class TerminalManager:
    def __init__(self, workspace: str, sandbox):
        self.workspace = workspace
        self.sandbox = sandbox
        self.term_dir = Path(workspace) / ".chisel" / "terms"
        self.term_dir.mkdir(parents=True, exist_ok=True)
        self.terms: dict[str, dict] = {}

    # --- 内部 --------------------------------------------------------------

    def _log_path(self, name: str) -> Path:
        return self.term_dir / f"{_safe_name(name)}.log"

    def _docker_pid(self, name: str, command: str) -> str:
        """容器内后台启动，返回 pid 字符串。

        必须用 setsid 脱离 exec 的进程组：docker exec 的会话退出时会清理后台进程，
        不脱离的话长驻进程会随 exec 结束一起被杀。
        """
        log_inside = f"/workspace/.chisel/terms/{_safe_name(name)}.log"
        inner = (
            f"mkdir -p /workspace/.chisel/terms && "
            f"setsid nohup sh -c {shlex.quote(command)} > {log_inside} 2>&1 < /dev/null & echo $!"
        )
        out = self.sandbox.run_raw(inner, timeout=30)
        body = out.split("[exit code")[0].strip()
        lines = [ln for ln in body.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""

    # --- 对外 --------------------------------------------------------------

    def start(self, name: str, command: str, workdir: str | None = None) -> str:
        safe = _safe_name(name)
        log = self._log_path(safe)
        if log.exists():
            log.unlink()  # 重启时清空旧日志
        if self.sandbox is not None and getattr(self.sandbox, "name", "") == "docker":
            pid = self._docker_pid(safe, command)
            self.terms[safe] = {"log": log, "pid": pid, "cursor": 0}
            return f"已在沙盒容器中启动终端 {safe}（pid {pid}），日志：{log}"
        # 宿主后端（PYTHONUNBUFFERED 避免重定向到文件后输出滞后）
        f = open(log, "wb")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=workdir or self.workspace,
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.terms[safe] = {"log": log, "pid": str(proc.pid), "proc": proc, "cursor": 0}
        return f"已启动终端 {safe}（pid {proc.pid}），日志：{log}"

    def stream(self, name: str, max_chars: int = 4000) -> str:
        safe = _safe_name(name)
        if safe not in self.terms:
            return f"终端 {safe} 不存在（已启动：{', '.join(self.terms) or '无'}）"
        t = self.terms[safe]
        path = Path(t["log"])
        if not path.exists():
            return f"终端 {safe}：日志尚未产生，可能仍在启动。"
        data = path.read_bytes()
        new = data[t["cursor"]:]
        t["cursor"] = len(data)
        if not new:
            return f"终端 {safe}：暂无新输出（累计 {len(data)} 字符）"
        text = new.decode("utf-8", errors="replace")
        return f"终端 {safe} 新增输出：\n{text[:max_chars]}"

    def kill(self, name: str) -> str:
        safe = _safe_name(name)
        if safe not in self.terms:
            return f"终端 {safe} 不存在"
        t = self.terms[safe]
        if self.sandbox is not None and getattr(self.sandbox, "name", "") == "docker":
            pid = t.get("pid", "")
            self.sandbox.run_raw(
                f"kill -9 {shlex.quote(pid)} 2>/dev/null || pkill -9 -f {shlex.quote(safe)} 2>/dev/null; true",
                timeout=30,
            )
        else:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", t["pid"]],
                               capture_output=True, timeout=30)
            except Exception:
                pass
        self.terms.pop(safe, None)
        return f"已终止终端 {safe}。"

    def kill_all(self) -> None:
        for name in list(self.terms):
            try:
                self.kill(name)
            except Exception:
                pass


def _handle_terminal(ctx, args: dict) -> str:
    tm = ctx.ensure_terminal()
    action = args.get("action")
    name = args.get("name", "")
    if action == "start":
        command = args.get("command", "")
        if not command:
            return "terminal 失败：start 需要 command 参数"
        return tm.start(name, command)
    if action == "stream":
        return tm.stream(name, args.get("max_chars") or 4000)
    if action == "kill":
        return tm.kill(name)
    return f"terminal 失败：未知 action {action!r}，应为 start/stream/kill"


register_tool(TERMINAL_SCHEMA, _handle_terminal)
