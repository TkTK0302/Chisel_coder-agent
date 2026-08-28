"""工具层：工具 schema 聚合 + 本地执行。

设计来源：
  - 工具 schema 写法来自 Aider 的 editblock_func_coder.py 的 `functions`。
  - edit_file 的 SEARCH/REPLACE 精确匹配 + 只替换第一处，来自 Aider do_replace()；
    本项目自写增强为多策略：精确 → 去空行 → `...` 省略 → 缩进容错，并带失败诊断
    （参考 Aider 的缩进容错 / try_dotdotdots / find_similar_lines 思路）。
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
                           "original_lines 必须与文件现有内容一致（含缩进），只替换第一处匹配。"
                           "支持在 original_lines 中用独立的一行 \"...\" 表示省略中间代码。"
                           "用于小范围修改，避免整文件重写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "original_lines": {
                        "type": "string",
                        "description": "文件中要被替换的原始代码段（必须精确匹配；可用 ... 省略中间）",
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
        return f"未知工具: {name}"
    except Exception as e:
        return f"工具执行出错: {type(e).__name__}: {e}"


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
        raise ValueError(f"禁止访问工作目录之外: {path}")
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
        return "命令超时（120s），已终止。"


def _run_bash(command: str, workspace: str, ctx=None) -> str:
    # 危险命令先确认（OpenHands 的「执行前询问」思路）
    if is_dangerous(command) and not _confirm_for(ctx)(command):
        return "用户取消了这条命令，未执行。"
    # 有上下文时走沙盒（默认 auto：Docker 优先，失败降级宿主）
    if ctx is not None:
        return ctx.ensure_sandbox().run(command, workspace)
    return _host_bash(command, workspace)


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
    return f"已写入 {path}（{len(content)} 字符）"


def _edit_file(path: str, original: str, updated: str, workspace: str, ctx=None) -> str:
    """SEARCH/REPLACE：多策略匹配 + 只替换第一处 + 失败诊断。"""
    _before_write(ctx, path)
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
        matched = "追加到末尾"
    else:
        result = _apply_edit(content, original, updated)
        if result is None:
            return _edit_failure_hint(path, original, content)
        new_content, matched = result

    p.write_text(new_content, encoding="utf-8")
    diff = _diff(content, new_content, path)
    return f"已编辑 {path}（策略：{matched}）\n{diff}"


# --- edit_file 多策略匹配（参考 Aider do_replace 思路，自写） -----------------


def _apply_edit(content: str, original: str, updated: str):
    """依次尝试 精确 → 去空行 → ...省略 → 缩进容错。返回 (新内容, 策略名) 或 None。"""
    # 1. 精确匹配（原语义：只替换第一处）
    if original in content:
        return content.replace(original, updated, 1), "精确匹配"
    # 2. 去首尾空行后精确匹配（模型常多打/漏打空行）
    o_trim = original.strip("\n")
    if o_trim and o_trim != original and o_trim in content:
        return content.replace(o_trim, updated, 1), "去空行匹配"
    # 3. ... 省略匹配（original 中用独立的 ... 行表示省略中间代码）
    if "..." in original or "…" in original:
        rx = _build_elision_re(original)
        if rx:
            m = rx.search(content)
            if m:
                return content[: m.start()] + updated + content[m.end():], "…省略匹配"
    # 4. 缩进容错：逐行去掉前导空白后比对（处理模型缩进漂移）
    result = _indent_tolerant_match(content, original, updated)
    if result:
        return result
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
    """缩进容错：跳过每行前导空白后比对；命中后用内容实际缩进重排 updated。"""
    cl = content.splitlines()
    o_lines = [ln for ln in original.splitlines() if ln.strip()]
    if not o_lines:
        return None
    o_stripped = [ln.lstrip() for ln in o_lines]
    n = len(o_stripped)
    for start in range(len(cl) - n + 1):
        window = cl[start : start + n]
        if [ln.lstrip() for ln in window] == o_stripped:
            indent = window[0][: len(window[0]) - len(window[0].lstrip())]
            u_lines = [(indent + ln.lstrip()) if ln.strip() else ln for ln in updated.splitlines()]
            new_lines = cl[:start] + u_lines + cl[start + n :]
            return "\n".join(new_lines), "缩进容错匹配"
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
                    hint = f"\n文件第 {ln_no} 行最接近你的 SEARCH：{line}"
                    break
    return (
        f"edit_file 失败：original_lines 未在 {path} 中匹配到。\n"
        f"已尝试：精确匹配 → 去空行 → …省略 → 缩进容错，均未命中。{hint}\n"
        f"请用 read_file 查看文件当前真实内容，再重试（注意缩进、空白、标点）。"
    )


def _diff(old: str, new: str, path: str) -> str:
    """生成 unified diff，让改动一目了然（来源：Cline 的「可见性」思路）。"""
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} 修改前", tofile=f"{path} 修改后", lineterm="",
    )
    return "\n".join(diff)
