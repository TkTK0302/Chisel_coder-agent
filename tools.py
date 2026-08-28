"""工具层：工具 schema 聚合 + 本地执行。

设计来源：
  - 工具 schema 写法来自 Aider 的 editblock_func_coder.py 的 `functions`。
  - edit_file 的 SEARCH/REPLACE 精确匹配 + 只替换第一处，来自 Aider do_replace()；
    本项目自写增强为多策略：精确 → 去空行 → `...` 省略 → 缩进容错，并带失败诊断。
  - 危险命令确认来自 OpenHands confirmation mode。

结构：
  - BASE_TOOLS：4 个基础工具（bash / read_file / write_file / edit_file）
  - EXTRA_TOOLS + register_tool()：各领域模块把自己的工具 schema 与处理器注册进来，
    all_tools() 动态聚合 —— 单文件不膨胀。
  - execute_tool(name, args, workspace, ctx)：按名字分发执行，ctx 是 ExecutionContext。
"""

from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

# --- 基础工具 schema ---------------------------------------------------------

BASE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command. Use for running scripts, tests, compilation, file operations, "
                           "and any command-line task. Short-lived commands run directly; for long-running "
                           "processes like web servers, use the terminal tool instead. "
                           "The command runs inside the workspace directory, in a Docker sandbox by default "
                           "(safe isolation). Returns stdout, stderr, and the exit code. "
                           "Output is truncated at ~3000 characters to save context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the content of a text file at the given path. Use this to inspect source code, "
                           "configuration files, or any text file. When you know multiple files you need, "
                           "read them together and call this tool in the same response as other independent "
                           "tool calls. Returns the full file content or an error message if the file doesn't "
                           "exist. Path is relative to the workspace directory; path traversal is blocked for security.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file, relative to the workspace directory"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the given content. "
                           "Use this when creating a new file from scratch, or when a file needs a complete rewrite. "
                           "For small, targeted changes, use edit_file instead — it's more precise and saves tokens. "
                           "Parent directories are created automatically if they don't exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file, relative to the workspace directory"},
                    "content": {"type": "string", "description": "The complete new content of the file"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Apply a precise change to an existing file using SEARCH/REPLACE. "
                           "The original_lines must exactly match the current file content, including all "
                           "whitespace and indentation. Only the first occurrence is replaced. "
                           "Supports '...' as a single line to elide intermediate code. "
                           "For small, targeted changes; for large rewrites, use write_file instead. "
                           "If the match fails, the tool tries fuzzy strategies (trim blank lines, elision, "
                           "indent tolerance) and provides a diagnostic hint. "
                           "If all strategies fail, read the file again to see the actual content. "
                           "Returns a unified diff showing the change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file, relative to the workspace directory"},
                    "original_lines": {
                        "type": "string",
                        "description": "The exact stretch of code to replace. Must match the file content exactly. "
                                       "Use '...' on a single line to indicate code you are omitting between surrounding lines.",
                    },
                    "updated_lines": {
                        "type": "string",
                        "description": "The new code to replace the original_lines with.",
                    },
                },
                "required": ["path", "original_lines", "updated_lines"],
            },
        },
    },
]

# --- 扩展工具注册表 ----------------------------------------------------------

EXTRA_TOOLS: list[dict] = []
EXTRA_HANDLERS: dict[str, object] = {}


def register_tool(schema: dict, handler) -> None:
    """领域模块把自己的工具 schema + 处理器注册进来。模块 import 时调用。"""
    EXTRA_TOOLS.append(schema)
    EXTRA_HANDLERS[schema["function"]["name"]] = handler


def all_tools() -> list[dict]:
    """动态聚合：基础工具 + 所有已注册的扩展工具。"""
    return BASE_TOOLS + EXTRA_TOOLS


def available_tool_names() -> list[str]:
    return [t["function"]["name"] for t in all_tools()]


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
    # IGNORECASE：Windows 上模型可能输出大写命令（如 DEL /S、RMDIR /S），都要拦
    return any(re.search(p, command, re.IGNORECASE) for p in DANGEROUS_PATTERNS)


# --- 工具执行 ---------------------------------------------------------------


def execute_tool(name: str, args: dict, workspace: str, ctx=None) -> str:
    """按名字分发执行，返回给模型的文本结果。ctx 是 ExecutionContext（可选）。

    工具执行出错时把异常回填给模型，让它读取真实内容后自纠。
    """
    try:
        if name == "bash":
            return _run_bash(args["command"], workspace, ctx)
        if name == "read_file":
            return _read_file(args["path"], workspace)
        if name == "write_file":
            return _write_file(args["path"], args["content"], workspace, ctx)
        if name == "edit_file":
            return _edit_file(args["path"], args["original_lines"], args["updated_lines"], workspace, ctx)
        if ctx is not None and name in EXTRA_HANDLERS:
            return EXTRA_HANDLERS[name](ctx, args)
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool execution error: {type(e).__name__}: {e}"


def _confirm_for(ctx):
    if ctx is not None and ctx.confirm_dangerous is not None:
        return ctx.confirm_dangerous
    return lambda command: False  # 无上下文时默认拒绝（安全第一）


def _resolve(workspace: str, path: str) -> Path:
    """把相对路径解析到工作目录内，并阻止路径穿越（../ 逃逸）。"""
    p = Path(workspace) / path
    resolved = p.resolve()
    ws = Path(workspace).resolve()
    if not str(resolved).startswith(str(ws)):
        raise ValueError(f"Access denied: path escapes workspace: {path}")
    return resolved


def _host_bash(command: str, workspace: str) -> str:
    """宿主机一次性执行（P3 沙盒接入前的默认后端）。"""
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
        return "Command timed out (120s)."


def _run_bash(command: str, workspace: str, ctx=None) -> str:
    # 危险命令先确认（OpenHands 的「执行前询问」思路）
    if is_dangerous(command) and not _confirm_for(ctx)(command):
        return "User cancelled the command."
    # 有上下文时走沙盒（默认 auto：Docker 优先，失败降级宿主）
    if ctx is not None:
        return ctx.ensure_sandbox().run(command, workspace)
    return _host_bash(command, workspace)


def _read_file(path: str, workspace: str) -> str:
    p = _resolve(workspace, path)
    if not p.exists():
        return f"File not found: {path}"
    if not p.is_file():
        return f"Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="utf-8", errors="replace")
    return content if content else "(empty file)"


def _before_write(ctx, path: str) -> None:
    """写文件前的版本安全网钩子（P4 gitops 接入后生效）。"""
    if ctx is not None and getattr(ctx, "git", None) is not None:
        try:
            ctx.git.before_write(path)
        except Exception:
            pass  # 快照失败不阻断写入


def _write_file(path: str, content: str, workspace: str, ctx=None) -> str:
    _before_write(ctx, path)
    p = _resolve(workspace, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {path} ({len(content)} chars)"


def _edit_file(path: str, original: str, updated: str, workspace: str, ctx=None) -> str:
    """SEARCH/REPLACE：多策略匹配 + 只替换第一处 + 失败诊断。"""
    _before_write(ctx, path)
    p = _resolve(workspace, path)
    if not p.exists():
        # 空 original = 新建文件
        if not original.strip():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(updated, encoding="utf-8")
            return f"Created {path} ({len(updated)} chars)"
        return f"File not found: {path}"

    content = p.read_text(encoding="utf-8")
    if not original.strip():
        # 空 original 对已存在文件 = 追加到末尾
        new_content = content + updated
        matched = "appended"
    else:
        result = _apply_edit(content, original, updated)
        if result is None:
            return _edit_failure_hint(path, original, content)
        new_content, matched = result

    p.write_text(new_content, encoding="utf-8")
    diff = _diff(content, new_content, path)
    return f"Edited {path} (strategy: {matched})\n{diff}"


# --- edit_file 多策略匹配（参考 Aider do_replace 思路，自写） -----------------


def _apply_edit(content: str, original: str, updated: str):
    """依次尝试 精确 → 去空行 → ...省略 → 缩进容错 → 模糊匹配。返回 (新内容, 策略名) 或 None。"""
    # 1. 精确匹配（原语义：只替换第一处）
    if original in content:
        return content.replace(original, updated, 1), "exact"
    # 2. 去首尾空行后精确匹配（模型常多打/漏打空行）
    o_trim = original.strip("\n")
    if o_trim and o_trim != original and o_trim in content:
        return content.replace(o_trim, updated, 1), "trimmed"
    # 3. ... 省略匹配（original 中用独立的 ... 行表示省略中间代码）
    if "..." in original or "…" in original:
        rx = _build_elision_re(original)
        if rx:
            m = rx.search(content)
            if m:
                return content[: m.start()] + updated + content[m.end():], "elision"
    # 4. 缩进容错：逐行去掉前导空白后比对（处理模型缩进漂移）
    result = _indent_tolerant_match(content, original, updated)
    if result:
        return result
    # 5. diff-match-patch 模糊匹配：允许 SEARCH 与原文有微小差异时仍能匹配
    result = _fuzzy_match_edit(content, original, updated)
    if result:
        return result
    return None


def _fuzzy_match_edit(content: str, original: str, updated: str):
    """diff-match-patch 模糊匹配：允许空格/缩进/换行差异时仍能匹配。

    策略：把 original→updated 做成 patch，以 0.3 的阈值应用到 content 上。
    如果至少一个 hunk 命中，就接受结果。
    """
    try:
        import diff_match_patch as dmp_module

        dmp = dmp_module.diff_match_patch()
        dmp.Match_Threshold = 0.2
        patch = dmp.patch_make(original, updated)
        result, successes = dmp.patch_apply(patch, content)
        if successes and any(successes):
            return result, "fuzzy"
    except Exception:
        pass
    return None


def _build_elision_re(original: str):
    """把含 ... 行的 original 切成若干段，段间用 [\\s\\S]*? 衔接成正则。"""
    segs, cur = [], []
    for line in original.split("\n"):
        if line.strip() in ("...", "…"):
            if cur:
                segs.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        segs.append("\n".join(cur))
    segs = [s for s in segs if s.strip()]
    if len(segs) < 2:
        return None
    pattern = r"[\s\S]*?".join(re.escape(s) for s in segs)
    return re.compile(pattern, re.DOTALL)


def _indent_tolerant_match(content: str, original: str, updated: str):
    """缩进容错：跳过每行前导空白后比对；命中后直接用 updated 替换（不重排缩进，
    因为模型写的 updated 缩进通常是对的，只是 SEARCH 的缩进不准）。"""
    cl = content.splitlines()
    o_lines = [ln for ln in original.splitlines() if ln.strip()]
    if not o_lines:
        return None
    o_stripped = [ln.lstrip() for ln in o_lines]
    n = len(o_stripped)
    for start in range(len(cl) - n + 1):
        window = cl[start : start + n]
        if [ln.lstrip() for ln in window] == o_stripped:
            new_lines = cl[:start] + updated.splitlines() + cl[start + n :]
            return "\n".join(new_lines), "indent-tolerant"
    return None


def _edit_failure_hint(path: str, original: str, content: str) -> str:
    """失败诊断：尝试告诉模型真实文件里最接近的一行，引导它 read_file 后重试。"""
    first = next((ln for ln in original.splitlines() if ln.strip()), "").strip()
    hint = ""
    if first:
        matches = difflib.get_close_matches(first, [ln.strip() for ln in content.splitlines()], n=1, cutoff=0.5)
        if matches:
            for ln_no, line in enumerate(content.splitlines(), 1):
                if line.strip() == matches[0]:
                    hint = f"\nClosest match at line {ln_no}: {line}"
                    break
    return (
        f"edit_file failed: original_lines not found in {path}. "
        f"Tried strategies: exact → trimmed → elision → indent-tolerant → fuzzy. "
        f"All failed.{hint}\n"
        f"Use read_file to see the actual file content, then retry (check indentation, whitespace, punctuation)."
    )


def _diff(old: str, new: str, path: str) -> str:
    """生成 unified diff，让改动一目了然（来源：Cline 的「可见性」思路）。"""
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm="",
    )
    return "\n".join(diff)