"""仓库地图（Repo Map）：自动把项目代码结构注入到 system prompt。

支持多语言（Python ast + 其他语言正则提取）。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

_SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache",
              ".aider.tags.cache.v4", "venv", "env"}
_SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp"}


def get_repo_map(workspace: str, max_chars: int = 2000) -> str:
    parts: list[str] = ["Project structure (functions/classes):"]
    total = 0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in sorted(files):
            if total > max_chars:
                break
            rel = os.path.relpath(os.path.join(root, f), workspace).replace("\\", "/")
            ext = Path(f).suffix
            if ext == ".py":
                symbols = _extract_python(os.path.join(root, f))
            elif ext in _SOURCE_EXTS:
                symbols = _extract_regex(os.path.join(root, f), ext)
            else:
                continue
            if symbols:
                line = f"  {rel}:  {', '.join(symbols)}"
                total += len(line) + 1
                if total > max_chars:
                    break
                parts.append(line)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def _extract_python(path: str) -> list[str]:
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return []
    symbols = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            symbols.append(f"{kind} {node.name}")
    return symbols[:30]


# 多语言函数/类定义的正则模式
_LANG_PATTERNS = {
    ".js": [
        (r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", "def"),
        (r"(?:export\s+)?class\s+(\w+)", "class"),
        (r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(.*\)\s*=>", "arrow"),
    ],
    ".ts": [
        (r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", "def"),
        (r"(?:export\s+)?class\s+(\w+)", "class"),
        (r"(?:export\s+)?interface\s+(\w+)", "interface"),
        (r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*:", "typed"),
        (r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*\(.*\)\s*=>", "arrow"),
    ],
    ".go": [
        (r"func\s+(\w+)", "func"),
        (r"type\s+(\w+)\s+struct", "struct"),
        (r"type\s+(\w+)\s+interface", "interface"),
    ],
    ".rs": [
        (r"fn\s+(\w+)", "fn"),
        (r"struct\s+(\w+)", "struct"),
        (r"enum\s+(\w+)", "enum"),
        (r"impl\s+(\w+)", "impl"),
        (r"trait\s+(\w+)", "trait"),
    ],
    ".java": [
        (r"(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+(\w+)", "class"),
        (r"(?:public|private|protected)?\s*(?:static\s+)?\w+\s+(\w+)\s*\(.*\)", "method"),
    ],
    ".c": [ (r"^\w+\s+(\w+)\s*\(.*\)\s*\{", "func") ],
    ".cpp": [ (r"^\w+\s+(\w+)\s*\(.*\)\s*\{", "func"), (r"class\s+(\w+)", "class") ],
    ".h": [ (r"^\w+\s+(\w+)\s*\(.*\)", "decl"), (r"class\s+(\w+)", "class") ],
    ".hpp": [ (r"^\w+\s+(\w+)\s*\(.*\)", "decl"), (r"class\s+(\w+)", "class") ],
}


def _extract_regex(path: str, ext: str) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    symbols = []
    patterns = _LANG_PATTERNS.get(ext, [])
    seen = set()
    for pattern, kind in patterns:
        for m in re.finditer(pattern, text, re.MULTILINE):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                symbols.append(f"{kind} {name}")
    return symbols[:20]