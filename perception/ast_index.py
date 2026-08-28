"""AST 符号索引：基于 Python 内置 ast 的代码导航（自研"轻量 LSP"）。

设计来源（借鉴 Aider RepoMap 的"签名地图"与 LSP 的 definition/references 语义，自写）：
  - 用 Python 内置 ast 解析项目里的 .py 文件，收集 函数/类定义（名字、签名、行号）与
    Name 标识符引用位置，建倒排索引。
  - 提供 definition / references / symbols 三个动作；非 Python 文件用 grep 兜底。
  - 懒建索引 + mtime 检测：文件变了才全量重建（小项目足够，零第三方依赖）。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from tools import register_tool

NAV_SCHEMA = {
    "type": "function",
    "function": {
        "name": "code_navigate",
        "description": "Navigate the codebase using Python AST analysis and grep fallback. "
                       "definition: find where a function or class is defined (file path, line number, signature). "
                       "references: find all usages of a symbol across the codebase. "
                       "symbols: list all functions and classes in the project, optionally filtered by file or name. "
                       "For Python files, uses AST parsing for accurate results; for other files, "
                       "falls back to word-boundary grep. "
                       "Call this tool in the same response as other independent tool calls.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["definition", "references", "symbols"]},
                "symbol": {"type": "string", "description": "action=definition/references 时的符号名（函数/类名）"},
                "path": {"type": "string", "description": "action=symbols 时限定文件，省略则列出全项目"},
                "pattern": {"type": "string", "description": "action=symbols 时按符号名子串过滤"},
            },
            "required": ["action"],
        },
    },
}

_SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache", "tests"}


class ASTIndex:
    def __init__(self, workspace: str):
        self.workspace = workspace
        self.files_mtime: dict[str, float] = {}
        self.defs: dict[str, list[dict]] = {}
        self.refs: dict[str, list[dict]] = {}
        self.symbol_list: list[tuple] = []
        self._loaded = False

    # --- 索引构建 ----------------------------------------------------------

    def _py_files(self):
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)

    def refresh(self) -> None:
        files = {p: os.path.getmtime(p) for p in self._py_files()}
        if self._loaded and files == self.files_mtime:
            return
        self._loaded = True
        self.files_mtime = files
        self.defs.clear()
        self.refs.clear()
        self.symbol_list = []
        for p in files:
            self._parse(p)

    def _parse(self, path: str) -> None:
        try:
            src = Path(path).read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            return
        rel = os.path.relpath(path, self.workspace).replace("\\", "/")
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                sig = self._signature(node)
                self.defs.setdefault(node.name, []).append(
                    {"file": rel, "line": node.lineno, "kind": type(node).__name__,
                     "signature": sig, "end_line": getattr(node, "end_lineno", node.lineno)}
                )
                self.symbol_list.append((rel, node.lineno, "def", node.name, sig))
            elif isinstance(node, ast.Name) and isinstance(getattr(node, "ctx", None), ast.Load):
                self.refs.setdefault(node.id, []).append(
                    {"file": rel, "line": node.lineno, "context": self._ctx_line(lines, node.lineno)}
                )

    @staticmethod
    def _signature(node) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            try:
                args = ast.unparse(node.args)
            except Exception:
                args = ""
            return f"{prefix} {node.name}({args})"
        return f"class {node.name}"

    @staticmethod
    def _ctx_line(lines: list[str], lineno: int) -> str:
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()[:80]
        return ""

    # --- 查询 --------------------------------------------------------------

    def find_definition(self, symbol: str) -> str:
        self.refresh()
        defs = self.defs.get(symbol)
        if defs:
            return "\n".join(f"{d['file']}:{d['line']}  {d['signature']}" for d in defs[:20])
        return self._grep(symbol)

    def find_references(self, symbol: str) -> str:
        self.refresh()
        lines = [f"{r['file']}:{r['line']}: {r['context']}" for r in self.refs.get(symbol, [])]
        for d in self.defs.get(symbol, []):
            lines.append(f"{d['file']}:{d['line']}  ← 定义处 {d['signature']}")
        if lines:
            return "\n".join(lines[:60])
        return self._grep(symbol)

    def list_symbols(self, path: str | None = None, pattern: str | None = None) -> str:
        self.refresh()
        rows = []
        for rel, line, kind, name, sig in sorted(self.symbol_list, key=lambda x: (x[0], x[1])):
            if path and rel != path:
                continue
            if pattern and pattern not in name:
                continue
            rows.append(f"{rel}:{line}  {sig}")
        if not rows:
            return "(未找到匹配的 Python 符号；非 Python 文件请用 bash grep)"
        return "\n".join(rows[:200])

    def _grep(self, symbol: str) -> str:
        """非 Python / 未建索引时的单词边界 grep 兜底。"""
        matches = []
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files:
                p = Path(root) / f
                rel = os.path.relpath(p, self.workspace).replace("\\", "/")
                try:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if re.search(rf"\b{re.escape(symbol)}\b", line):
                            matches.append(f"{rel}:{i}: {line.strip()[:80]}")
                            if len(matches) >= 50:
                                return "\n".join(matches)
                except OSError:
                    continue
        return f"未找到符号 {symbol}"


def _handle_code_navigate(ctx, args: dict) -> str:
    idx = ctx.ensure_ast()
    action = args.get("action")
    symbol = args.get("symbol", "")
    if action == "definition":
        return idx.find_definition(symbol) if symbol else "code_navigate: 需要 symbol 参数"
    if action == "references":
        return idx.find_references(symbol) if symbol else "code_navigate: 需要 symbol 参数"
    if action == "symbols":
        return idx.list_symbols(args.get("path"), args.get("pattern"))
    return f"code_navigate 失败：未知 action {action!r}"


register_tool(NAV_SCHEMA, _handle_code_navigate)
