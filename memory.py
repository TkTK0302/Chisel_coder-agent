"""记忆模块：持久记忆 + 结构化 key-value + 去重 + AI 可写 + 分类 + 搜索 + checkpoint。

改进：
  - 记忆分类：按 scope (global/project/session) 和 type (preference/note/checkpoint)
  - 记忆搜索：FTS5+BM25 搜索记忆内容
  - 自动 checkpoint：每次任务完成时自动写 checkpoint.md
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from tools import register_tool

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
    m = re.match(r"^- (.+?):\s*(.*)$", entry.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def add_memory(workspace: str, text: str, scope: str = "project", mem_type: str = "preference") -> str:
    """写入一条偏好。支持 key: value 格式和纯文本。"""
    p = memory_path(workspace)
    entry_line = f"- {text}\n"

    if not p.exists():
        p.write_text(entry_line, encoding="utf-8")
        return f"Remembered: {text}"

    existing = p.read_text(encoding="utf-8")
    kv = _parse_key(entry_line)
    if kv:
        key, _ = kv
        lines = existing.splitlines(keepends=True)
        replaced = False
        new_lines = []
        for line in lines:
            line_kv = _parse_key(line)
            if line_kv and line_kv[0] == key:
                new_lines.append(entry_line)
                replaced = True
            else:
                new_lines.append(line)
        if replaced:
            p.write_text("".join(new_lines), encoding="utf-8")
            _auto_merge(workspace, p)  # 合并后优化
            return f"Remembered: {key} = {_}"
    if entry_line not in existing:
        p.write_text(existing + entry_line, encoding="utf-8")
        _auto_merge(workspace, p)  # 合并后优化
        return f"Remembered: {text}"
    return f"Already exists (skipped): {text}"


def _auto_merge(workspace: str, p: Path) -> None:
    """记忆自动合并：当 MEMORY.md 超过 30 行时，合并相似条目。"""
    content = p.read_text(encoding="utf-8")
    lines = content.splitlines()
    if len(lines) <= 30:
        return

    # 按 key 合并：相同 key 的保留最新值
    entries = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("- "):
            continue
        text = line[2:].strip()
        kv = _parse_key(line)
        if kv:
            entries[kv[0]] = kv[1]  # key → value，后写入的覆盖先写入的
        else:
            # 纯文本去重
            if text not in entries.values():
                entries[text] = text

    merged = "\n".join(f"- {k}: {v}" if k != v else f"- {v}" for k, v in entries.items())
    p.write_text(merged + "\n", encoding="utf-8")


# --- 记忆搜索（FTS5+BM25）--------------------------------------------------

_MEMORY_DB = {}  # workspace -> db_path


def _get_memory_db(workspace: str) -> sqlite3.Connection:
    """获取记忆搜索的 SQLite 数据库。"""
    if workspace not in _MEMORY_DB:
        db_path = Path(workspace) / ".chisel" / "memory_search.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS memories (scope TEXT, type TEXT, content TEXT)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content)")
        conn.commit()
        _MEMORY_DB[workspace] = conn
        # 初始索引现有 MEMORY.md
        _reindex_memory(workspace, conn)
    return _MEMORY_DB[workspace]


def _reindex_memory(workspace: str, conn: sqlite3.Connection) -> None:
    """把 MEMORY.md 的内容索引到 FTS5。"""
    p = memory_path(workspace)
    if not p.exists():
        return
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        return
    conn.execute("DELETE FROM memories")
    conn.execute("DELETE FROM memories_fts")
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("- "):
            text = line[2:].strip()
            scope = "project"
            mem_type = "preference"
            conn.execute("INSERT INTO memories (scope, type, content) VALUES (?,?,?)",
                        (scope, mem_type, text))
    conn.commit()
    # 索引到 FTS
    rows = conn.execute("SELECT rowid, content FROM memories").fetchall()
    for rowid, content in rows:
        conn.execute("INSERT INTO memories_fts (rowid, content) VALUES (?,?)", (rowid, content))
    conn.commit()


def search_memory(workspace: str, query: str, top_k: int = 5) -> str:
    """搜索记忆内容。"""
    try:
        conn = _get_memory_db(workspace)
        rows = conn.execute(
            "SELECT m.scope, m.type, m.content, bm25(memories_fts) AS r "
            "FROM memories_fts f JOIN memories m ON f.rowid = m.rowid "
            "WHERE memories_fts MATCH ? ORDER BY r LIMIT ?",
            (query, top_k),
        ).fetchall()
        if not rows:
            return "No matching memories found."
        parts = [f"Found {len(rows)} memories:"]
        for scope, mem_type, content, _ in rows:
            parts.append(f"  [{scope}/{mem_type}] {content}")
        return "\n".join(parts)
    except Exception as e:
        return f"Memory search error: {e}"


# --- 自动 checkpoint ---------------------------------------------------------

def save_checkpoint(workspace: str, task: str, result: str) -> str:
    """任务完成时自动保存 checkpoint。"""
    from datetime import datetime
    check_dir = Path(workspace) / ".chisel" / "checkpoints"
    check_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"checkpoint_{timestamp}.md"
    content = f"# Checkpoint {timestamp}\n\n## Task\n{task}\n\n## Result\n{result}\n"
    (check_dir / filename).write_text(content, encoding="utf-8")
    return f"Checkpoint saved: {filename}"


# --- AI 可写的 remember 工具 ------------------------------------------------

REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "Remember a user preference or important information persistently across sessions. "
                       "Call this when you notice a clear preference, convention, or requirement.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Preference name, e.g. 'language', 'style'"},
                "value": {"type": "string", "description": "Preference value, e.g. 'Python', 'type hints'"},
            },
            "required": ["key", "value"],
        },
    },
}

MEMORY_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_search",
        "description": "Search remembered preferences and information from previous sessions. "
                       "Use this to recall user preferences or conventions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "description": "Number of results, default 5"},
            },
            "required": ["query"],
        },
    },
}


def _handle_remember(ctx, args: dict) -> str:
    key = args.get("key", "").strip()
    value = args.get("value", "").strip()
    if not key or not value:
        return "remember: both key and value are required."
    return add_memory(ctx.workspace, f"{key}: {value}")


def _handle_memory_search(ctx, args: dict) -> str:
    return search_memory(ctx.workspace, args.get("query", ""), args.get("top_k") or 5)


register_tool(REMEMBER_SCHEMA, _handle_remember)
register_tool(MEMORY_SEARCH_SCHEMA, _handle_memory_search)