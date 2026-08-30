"""记忆模块：持久记忆 + 结构化 key-value + 去重 + AI 可写。

三种写入方式：
  1. --memory "key: value"  CLI 参数（用户写入）
  2. --memory "纯文本偏好"   CLI 参数（用户写入，无 key）
  3. remember(key="language", value="Python")  AI 工具（AI 自动写入）

存储格式（MEMORY.md）：
  - language: Python
  - test_first: true
  - 纯文本偏好

读取时按 key 去重，同 key 覆盖。纯文本行（无冒号）追加并去重。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools import register_tool

# 匹配 "key: value" 格式
_KEY_VALUE_RE = re.compile(r"^- (.+?):\s*(.*)$", re.MULTILINE)


def memory_path(workspace: str) -> Path:
    return Path(workspace) / "MEMORY.md"


def load_memory(workspace: str) -> str:
    """启动时读 MEMORY.md，返回给 system prompt 用。"""
    p = memory_path(workspace)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def _parse_key(entry: str) -> tuple[str, str] | None:
    """解析 "key: value" 格式，返回 (key, value) 或 None。"""
    m = re.match(r"^- (.+?):\s*(.*)$", entry.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def add_memory(workspace: str, text: str) -> str:
    """写入一条偏好。支持 key: value 格式（同 key 覆盖）和纯文本（去重追加）。

    用法：
      add_memory(".", "language: Python")    → 结构化 key-value
      add_memory(".", "我喜欢用 Python")      → 纯文本
    """
    p = memory_path(workspace)
    entry_line = f"- {text}\n"

    if not p.exists():
        p.write_text(entry_line, encoding="utf-8")
        return f"已记住：{text}"

    existing = p.read_text(encoding="utf-8")

    # 检查是否是 key: value 格式
    kv = _parse_key(entry_line)
    if kv:
        key, _ = kv
        # 在现有内容中查找同 key 行
        lines = existing.splitlines(keepends=True)
        replaced = False
        new_lines = []
        for line in lines:
            line_kv = _parse_key(line)
            if line_kv and line_kv[0] == key:
                # 替换旧值
                new_lines.append(entry_line)
                replaced = True
            else:
                new_lines.append(line)
        if replaced:
            p.write_text("".join(new_lines), encoding="utf-8")
            return f"已记住：{key} = {_}"

    # 纯文本：去重追加
    if entry_line not in existing:
        p.write_text(existing + entry_line, encoding="utf-8")
        return f"已记住：{text}"
    else:
        return f"已存在（跳过重复）：{text}"


# --- AI 可写的 remember 工具 ------------------------------------------------

REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "Remember a user preference or important information persistently across sessions. "
                       "Call this when you notice a clear preference, convention, or requirement "
                       "that should be remembered for future tasks. "
                       "Use key:value format for structured preferences (e.g., language: Python).",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Preference name, e.g. 'language', 'style', 'test_framework'"},
                "value": {"type": "string", "description": "Preference value, e.g. 'Python', 'type hints', 'pytest'"},
            },
            "required": ["key", "value"],
        },
    },
}


def _handle_remember(ctx, args: dict) -> str:
    key = args.get("key", "").strip()
    value = args.get("value", "").strip()
    if not key or not value:
        return "remember: both key and value are required."
    return add_memory(ctx.workspace, f"{key}: {value}")


register_tool(REMEMBER_SCHEMA, _handle_remember)