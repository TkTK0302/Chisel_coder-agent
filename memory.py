"""记忆模块：把用户偏好持久化到 MEMORY.md，下次启动读进 system prompt。

设计来源：MiMo Code / OpenCode 的「持久记忆」思路 —— 用户偏好存本地文件，
每次会话启动时读出来注入到上下文，让 agent 记住跨会话的偏好。

简化后的实现（本项目考核不需要 MiMo 的 FTS 全文检索，只需要「存 + 读 + 注入」）：
  - add_memory()  对应 --memory 参数，把一条偏好追加到 MEMORY.md
  - load_memory() 启动时读取，拼进 system prompt
"""

from __future__ import annotations

from pathlib import Path


def memory_path(workspace: str) -> Path:
    return Path(workspace) / "MEMORY.md"


def load_memory(workspace: str) -> str:
    """启动时读 MEMORY.md，返回给 system prompt 用。"""
    p = memory_path(workspace)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def add_memory(workspace: str, text: str) -> str:
    """--memory 参数：追加一条用户偏好。"""
    p = memory_path(workspace)
    entry = f"- {text}\n"
    if p.exists():
        p.write_text(p.read_text(encoding="utf-8") + entry, encoding="utf-8")
    else:
        p.write_text(entry, encoding="utf-8")
    return f"已记住：{text}"
