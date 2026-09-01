"""项目规模检测：决定使用 single 模式（单 Agent 两阶段）还是 multi 模式（多 Agent 委托）。

策略（优先级从高到低）：
  1. 任务感知：分析任务描述，如果只涉及 1-2 个具体文件 → single
  2. 文件数 ≤ MIN_FILES_FOR_MULTI：强制 single
  3. 小项目（single）：文件数 < 30 且 总行数 < 3000 且 符号数 < 50
  4. 大项目（multi）：文件数 ≥ 30 或 总行数 ≥ 3000 或 符号数 ≥ 50

检测指标：
  - 文件数（仅统计 .py / .js / .ts / .go / .rs / .java / .c / .cpp）
  - 代码总行数
  - 顶层函数/类定义数（用 ast 粗略估计）
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

_SKIP_DIRS = {".git", ".chisel", "__pycache__", "node_modules", ".venv", ".pytest_cache",
              ".aider.tags.cache.v4", "venv", "env", ".env"}
_SOURCE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp"}

# 小项目阈值
MIN_FILES_FOR_MULTI = 5   # 文件数 ≤ 4 时强制 single，不检查行数/符号数
MAX_FILES = 30
MAX_LINES = 3000
MAX_SYMBOLS = 50


def detect_mode_from_task(task: str, workspace: str) -> str:
    """根据任务描述 + 工作目录综合判断模式。

    任务感知优先于文件数检测：
    - 任务明确提到 1-2 个具体文件 → single（即使工作目录有 100 个文件）
    - 任务提到"全部"/"整个项目"/"所有文件" → 用 workspace 检测
    - 任务描述模糊 → 用 workspace 检测
    """
    # 提取任务中提到的文件名（.py / .js / .ts 等）
    file_pattern = re.compile(r'\b([\w\-/]+\.(?:py|js|ts|tsx|jsx|go|rs|java|c|cpp|h|hpp|json|md|txt|yml|yaml|toml|cfg|ini))\b')
    mentioned = set()
    for m in file_pattern.finditer(task):
        fname = m.group(1).split("/")[-1]  # 只取文件名，去掉路径
        mentioned.add(fname)

    # 如果任务没提到任何具体文件 → 检查是否是多子任务（如 "3 个 bug"）
    if not mentioned:
        # 任务提到多个编号子任务 → multi（适合 Planning Agent 分解）
        multi_task_pattern = re.compile(r'(\d+)\s*[个項项]\s*(bug|问题|任务|错误|缺陷|功能|模块)')
        m = multi_task_pattern.search(task)
        if m and int(m.group(1)) >= 2:
            return "multi"
        return detect_mode(workspace)

    # 任务提到"全部/所有/整个项目/重构" → 用 workspace 检测
    broad_keywords = ["全部", "所有", "整个", "每个", "重构", "refactor", "all files",
                      "every file", "整个项目", "整个仓库", "codebase", "whole project"]
    task_lower = task.lower()
    if any(kw in task_lower for kw in broad_keywords):
        return detect_mode(workspace)

    # 验证提到的文件是否存在于工作目录
    existing = set()
    for fname in mentioned:
        for root, dirs, fnames in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            if fname in fnames:
                existing.add(fname)
                break

    # 任务提到 1-2 个文件且它们都存在 → single
    if existing and len(existing) <= 2:
        return "single"

    # 任务提到 3+ 个文件 → 用 workspace 检测
    if len(mentioned) >= 3:
        return detect_mode(workspace)

    # 其他情况 → 用 workspace 检测
    return detect_mode(workspace)


def detect_mode(workspace: str) -> str:
    """返回 'single'（小项目）或 'multi'（大项目）。"""
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
            # 早期退出：文件数超过上限直接判 multi
            if files > MAX_FILES:
                return "multi"
            if lines > MAX_LINES or symbols > MAX_SYMBOLS:
                # 但文件数 ≤ 2 时不判 multi — 单文件/双文件任务 single 更高效
                if files > MIN_FILES_FOR_MULTI:
                    return "multi"

    return "single"


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