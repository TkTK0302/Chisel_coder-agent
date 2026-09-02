"""交互式终端：启动/流式查看/终止长驻进程（如 web server）。

两种后端统一到"日志文件 + 游标"模型：
  - 宿主：Popen(command, shell=True) 把 stdout 重定向到 .chisel/terms/<name>.log。
  - Docker：容器内 nohup 后台运行，输出写到同一份日志（工作目录是 bind mount，
    宿主可直接读），pid 通过 echo $! 拿回。
stream 用字节游标返回"自上次读取后新增"的输出。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools import register_tool

TERMINAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Start, stream, or terminate a long-running process (e.g., a web server, background compilation). "
                       "Use this tool instead of bash for processes that need to stay alive. "
                       "Each terminal is identified by a name; you can have multiple terminals running simultaneously. "
                       "action=start launches a new process; action=stream reads recent output; "
                       "action=kill terminates the process. "
                       "For short-lived commands, use bash instead.",
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
        # Q3: 注册容器回收回调，容器销毁时清理失效的终端状态
        if hasattr(sandbox, "on_recycle"):
            sandbox.on_recycle(self._on_container_destroyed)

    # --- 内部 --------------------------------------------------------------

    def _log_path(self, name: str) -> Path:
        return self.term_dir / f"{_safe_name(name)}.log"

    def _on_container_destroyed(self) -> None:
        """Q3: 容器被回收时清空终端状态，避免残留失效的 pid 引用。"""
        self.terms.clear()

    # --- 对外 --------------------------------------------------------------

    def start(self, name: str, command: str, workdir: str | None = None) -> str:
        safe = _safe_name(name)
        log = self._log_path(safe)
        if log.exists():
            log.unlink()  # 重启时清空旧日志
        log_abs = str(log.resolve())
        # Q10: 通过 Sandbox 门面启动后台进程，不再判断后端类型
        pid = self.sandbox.start_background(safe, command, log_abs)
        self.terms[safe] = {"log": log, "pid": pid, "cursor": 0}
        return f"已在终端启动 {safe}（pid {pid}），日志：{log}"

    def stream(self, name: str, max_chars: int = 4000) -> str:
        safe = _safe_name(name)
        if safe not in self.terms:
            return f"终端 {safe} 不存在（已启动：{', '.join(self.terms) or '无'}）"
        t = self.terms[safe]
        path = Path(t["log"])
        if not path.exists():
            return f"终端 {safe}：日志尚未产生，可能仍在启动。"
        data = path.read_bytes()
        # Q14: 游标越界检查——日志被外部截断时重置游标
        cursor = t["cursor"]
        if cursor > len(data):
            cursor = 0
        new = data[cursor:]
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
        # Q10: 通过 Sandbox 门面终止进程，不再判断后端类型
        pid = t.get("pid", "")
        self.sandbox.kill_background(pid, safe)
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
