"""项目规模检测：决定使用 Cline 模式（小项目）还是 OpenHands 模式（大项目）。

策略：
  - 小项目（Cline）：文件数 < 30 且 总行数 < 3000 且 符号数 < 50
  - 大项目（OpenHands）：任意指标超过阈值

检测指标：
  - 文件数（仅统计 .py / .js / .ts / .go / .rs / .java / .c / .cpp）
  - 代码总行数
  - 顶层函数/类定义数（用 ast 粗略估计）
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

_SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache",
              ".aider.tags.cache.v4", "venv", "env", ".env"}
_SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp"}

# 小项目阈值
MAX_FILES = 30
MAX_LINES = 3000
MAX_SYMBOLS = 50


def detect_mode(workspace: str) -> str:
    """返回 'cline'（小项目）或 'openhands'（大项目）。"""
    files = 0
    lines = 0
    symbols = 0
    for root, dirs, fnames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in fnames:
            ext = Path(fname).suffix
            if ext not in _SOURCE_EXTS:
                continue
            files += 1
            fp = Path(root) / fname
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                lines += len(text.splitlines())
                if ext == ".py":
                    try:
                        tree = ast.parse(text)
                        symbols += sum(1 for n in tree.body
                                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                    except SyntaxError:
                        pass
            except OSError:
                pass
            if files > MAX_FILES or lines > MAX_LINES or symbols > MAX_SYMBOLS:
                return "openhands"

    return "cline"


def describe(workspace: str) -> str:
    """返回模式选择说明（用于日志）。"""
    mode = detect_mode(workspace)
    files = 0
    lines = 0
    for root, dirs, fnames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in fnames:
            if Path(fname).suffix in _SOURCE_EXTS:
                files += 1
                try:
                    lines += len((Path(root) / fname).read_text(encoding="utf-8", errors="replace").splitlines())
                except OSError:
                    pass
    return f"[{mode.upper()}] {files} files, ~{lines} lines"