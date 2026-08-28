"""工具层：工具 schema 定义 + 本地执行。

设计来源：
  - 工具 schema 的写法来自 Aider 的 coders/editblock_func_coder.py 的 `functions`
    （一个 dict(name, description, parameters) 描述一个函数）。
  - edit_file 的「精确匹配 + 只替换第一处」语义来自 Aider 的
    coders/editblock_coder.py -> do_replace()（SEARCH/REPLACE 的核心）。
  - bash 执行前的危险命令确认来自 OpenHands 的 confirmation mode 思路。

本项目提供 4 个工具，覆盖「读写文件 + 执行命令」这一 coding agent 的最小闭环：
  1. bash        执行 shell 命令（ls / cat / grep / git / python ...）
  2. read_file   读文件
  3. write_file  新建或覆盖文件
  4. edit_file   精确 SEARCH/REPLACE 改文件（Aider 编辑策略的移植）
"""

from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

# --- 工具 schema（OpenAI function calling 格式） ----------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行一条 shell 命令。用于查看目录(ls)、查看文件(cat)、"
                           "搜索(grep/find)、运行代码(python)、git 等。命令在工作目录内执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文本文件的内容。先 ls 看目录，再读具体文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "新建一个文件，或用给定内容整体覆盖一个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "content": {"type": "string", "description": "文件的完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "精确替换文件中的一段内容（SEARCH/REPLACE）。"
                           "original_lines 必须与文件现有内容逐字符一致（含缩进），"
                           "只替换第一处匹配。用于小范围修改，避免整文件重写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "original_lines": {
                        "type": "string",
                        "description": "文件中要被替换的原始代码段（必须精确匹配）",
                    },
                    "updated_lines": {
                        "type": "string",
                        "description": "替换后的新代码段",
                    },
                },
                "required": ["path", "original_lines", "updated_lines"],
            },
        },
    },
]


# --- 危险命令检测（来源：OpenHands confirmation mode 思路） -----------------

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b",
    r"\brm\s+-r\s",
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+--force\b",
    r"\bdrop\s+(table|database)\b",
    r"\bDELETE\s+FROM\b",
    # 写具体磁盘设备才算危险（排除 /dev/null，它是无害的丢弃输出）
    r">\s*/dev/(sd|hd|nvme|mmcblk|xvd|vd|disk)",
    r"\bformat\s+[a-z]:",
    r"\bdd\s+if=",
    # Windows 递归删除（演示环境是 Windows，让「安全设计」片段可控触发）
    r"\brd\s+/s\b",
    r"\bdel\s+/s\b",
    r"\brmdir\s+/s\b",
    r"Remove-Item\b.*-(Recurse|Force)",
]


def is_dangerous(command: str) -> bool:
    return any(re.search(p, command) for p in DANGEROUS_PATTERNS)


# --- 工具执行 ---------------------------------------------------------------


def execute_tool(name: str, args: dict, workspace: str, confirm_fn) -> str:
    """根据工具名分发执行，返回给模型的文本结果（字符串）。"""
    try:
        if name == "bash":
            return _run_bash(args["command"], workspace, confirm_fn)
        if name == "read_file":
            return _read_file(args["path"], workspace)
        if name == "write_file":
            return _write_file(args["path"], args["content"], workspace)
        if name == "edit_file":
            return _edit_file(args["path"], args["original_lines"], args["updated_lines"], workspace)
        return f"未知工具: {name}"
    except Exception as e:  # 工具执行出错 → 把错误回填给模型，让它自行修正
        return f"工具执行出错: {type(e).__name__}: {e}"


def _resolve(workspace: str, path: str) -> Path:
    """把相对路径解析到工作目录内，并阻止路径穿越（../ 逃逸）。"""
    p = Path(workspace) / path
    # 归一化后必须仍在 workspace 内
    resolved = p.resolve()
    ws = Path(workspace).resolve()
    if not str(resolved).startswith(str(ws)):
        raise ValueError(f"禁止访问工作目录之外: {path}")
    return resolved


def _run_bash(command: str, workspace: str, confirm_fn) -> str:
    # 危险命令先确认（OpenHands 的「执行前询问」思路）
    if is_dangerous(command) and not confirm_fn(command):
        return "用户取消了这条命令，未执行。"

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = proc.stdout
        err = proc.stderr
        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("[stderr] " + err.rstrip())
        parts.append(f"[exit code {proc.returncode}]")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "命令超时（120s），已终止。"


def _read_file(path: str, workspace: str) -> str:
    p = _resolve(workspace, path)
    if not p.exists():
        return f"文件不存在: {path}"
    if not p.is_file():
        return f"不是文件: {path}"
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="utf-8", errors="replace")
    return content if content else "(空文件)"


def _write_file(path: str, content: str, workspace: str) -> str:
    p = _resolve(workspace, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符）"


def _edit_file(path: str, original: str, updated: str, workspace: str) -> str:
    """SEARCH/REPLACE：来源 Aider do_replace() 的「精确匹配 + 只替换第一处」。"""
    p = _resolve(workspace, path)
    if not p.exists():
        # 空 original = 新建文件
        if not original.strip():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(updated, encoding="utf-8")
            return f"已新建 {path}（{len(updated)} 字符）"
        return f"文件不存在: {path}"

    content = p.read_text(encoding="utf-8")
    if not original.strip():
        # 空 original 对已存在文件 = 追加到末尾
        new_content = content + updated
    else:
        # 精确匹配，只替换第一处（Aider 语义）
        if original not in content:
            return (
                f"edit_file 失败：original_lines 未在 {path} 中精确匹配到。\n"
                f"请用 read_file 查看文件当前真实内容，再重试（注意缩进、空白、标点）。"
            )
        new_content = content.replace(original, updated, 1)

    p.write_text(new_content, encoding="utf-8")
    diff = _diff(content, new_content, path)
    return f"已编辑 {path}（SEARCH/REPLACE 成功）\n{diff}"


def _diff(old: str, new: str, path: str) -> str:
    """生成 unified diff，让改动一目了然（来源：Cline 的「可见性」思路）。"""
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} 修改前", tofile=f"{path} 修改后", lineterm="",
    )
    return "\n".join(diff)
