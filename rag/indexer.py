"""索引器：把工作目录的代码分块建进 SQLite（FTS5 全文 + chunks 元数据），按 mtime 对账。

设计来源（借鉴 MiMo memory/reconcile 的"mtime 指纹对账"思路，自写）：
  - 每个文件按 顶层函数/类（Python）或 固定行数（其它）切成 chunk。
  - chunks 表存结构化数据（行号区间 + 全文），chunks_fts 是 FTS5 全文索引（供 BM25）。
  - reconcile() 对比已存 mtime 与当前 mtime：变了才重索引该文件，删除的清理掉。
"""
from __future__ import annotations

import ast
import os
import sqlite3
from pathlib import Path

from rag.models import Chunk

_SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache", ".aider.tags.cache.v4"}
_TEXT_EXTS = {".py", ".txt", ".md", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".bat", ".html", ".css", ".js", ".ts", ".c", ".h", ".cpp", ".java", ".go", ".rs"}
_NONPY_CHUNK_LINES = 50


class Indexer:
    def __init__(self, workspace: str, db_path: str | Path):
        self.workspace = workspace
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # --- 数据库 ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " path TEXT, start_line INTEGER, end_line INTEGER, text TEXT)"
            )
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(path UNINDEXED, text)"
            )
            self._conn.commit()
        return self._conn

    # --- 文件扫描与分块 -----------------------------------------------------

    def files(self) -> dict[str, float]:
        """工作目录内所有可索引文件 -> mtime。"""
        result: dict[str, float] = {}
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                if Path(f).suffix in _TEXT_EXTS:
                    p = Path(root) / f
                    result[os.path.relpath(p, self.workspace).replace("\\", "/")] = p.stat().st_mtime
        return result

    def _chunk_file(self, rel: str) -> list[tuple[int, int, str]]:
        """把文件切成 (start_line, end_line, text) 列表。"""
        path = Path(self.workspace) / rel
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        lines = src.splitlines()
        if not lines:
            return []
        if rel.endswith(".py"):
            return self._chunk_python(src, lines)
        # 非 Python：固定行数分块
        chunks = []
        for start in range(0, len(lines), _NONPY_CHUNK_LINES):
            end = min(start + _NONPY_CHUNK_LINES, len(lines))
            chunks.append((start + 1, end, "\n".join(lines[start:end])))
        return chunks

    def _chunk_python(self, src: str, lines: list[str]) -> list[tuple[int, int, str]]:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return []
        chunks: list[tuple[int, int, str]] = []
        # 模块级代码（import 等）作为开头块
        module_end = tree.body[0].lineno - 1 if tree.body else 0
        if module_end >= 1:
            chunks.append((1, module_end, "\n".join(lines[0:module_end])))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                chunks.append((start, end, "\n".join(lines[start - 1:end])))
        return chunks

    # --- 对账（reconcile） ---------------------------------------------------

    def reconcile(self) -> None:
        conn = self._connect()
        current = self.files()
        stored = {p: m for p, m in conn.execute("SELECT path, mtime FROM files")}
        for path, mtime in current.items():
            if stored.get(path) != mtime:
                self._reindex_file(conn, path, mtime)
        for path in stored:
            if path not in current:
                conn.execute("DELETE FROM chunks WHERE path=?", (path,))
                conn.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
                conn.execute("DELETE FROM files WHERE path=?", (path,))
        conn.commit()

    def _reindex_file(self, conn, path: str, mtime: float) -> None:
        conn.execute("DELETE FROM chunks WHERE path=?", (path,))
        conn.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
        for start, end, text in self._chunk_file(path):
            cur = conn.execute(
                "INSERT INTO chunks (path, start_line, end_line, text) VALUES (?,?,?,?)",
                (path, start, end, text),
            )
            conn.execute(
                "INSERT INTO chunks_fts (rowid, path, text) VALUES (?,?,?)",
                (cur.lastrowid, path, text),
            )
        conn.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?,?)", (path, mtime))

    # --- 查询辅助 -----------------------------------------------------------

    def all_chunks(self) -> list[Chunk]:
        conn = self._connect()
        rows = conn.execute("SELECT path, start_line, end_line, text FROM chunks").fetchall()
        return [Chunk(path, start, end, text) for path, start, end, text in rows]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
