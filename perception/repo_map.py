"""仓库地图（Repo Map）：自动把项目代码结构注入到 system prompt。

设计来源（借鉴 Aider 的 repomap.py + tree-sitter 思路，但用 Python 内置 ast 实现）：
  Aider 的 RepoMap 用 tree-sitter 解析多语言，用 PageRank 排序函数重要性。
  本项目精简为：用 perception/ast_index 的 AST 符号表，按文件组织成紧凑文本，
  任务开始时自动注入到 AI 上下文 —— AI 不用先读文件就知道项目里有什么函数/类。

  面试辩护点："我们的 RepoMap 用了 Python 内置 ast，零外部依赖，对纯 Python 项目
  效果等价于 tree-sitter 方案，但在面试时可以直接说清楚原理。"
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def get_repo_map(workspace: str, max_chars: int = 2000) -> str:
    """生成项目代码结构签名摘要，注入到 system prompt。

    扫描工作目录下所有 .py 文件，提取函数/类定义，格式化为紧凑文本。
    非 Python 文件只列文件名。
    """
    parts: list[str] = ["项目结构（函数/类概览）："]
    _SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache", ".aider.tags.cache.v4"}
    total = 0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in sorted(files):
            if total > max_chars:
                break
            rel = os.path.relpath(os.path.join(root, f), workspace).replace("\\", "/")
            if f.endswith(".py"):
                symbols = _extract_symbols(os.path.join(root, f))
                if symbols:
                    line = f"  {rel}:  {', '.join(symbols)}"
                    total += len(line) + 1
                    if total > max_chars:
                        break
                    parts.append(line)
            else:
                # 非 Python 文件只列文件名
                line = f"  {rel}"
                total += len(line) + 1
                if total > max_chars:
                    break
                parts.append(line)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def _extract_symbols(path: str) -> list[str]:
    """用 ast 提取文件顶层函数和类名。"""
    import ast

    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return []
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            symbols.append(f"{kind} {node.name}")
    return symbols[:30]