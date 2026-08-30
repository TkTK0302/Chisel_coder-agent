"""Git 工具 + 版本安全网（auto-commit 快照 + 账本式 undo）。

设计来源（借鉴 Aider GitRepo / base_coder auto_commit / cmd_undo 思路，自写）：
  - before_write：每次 write/edit 前，若工作树有改动则先自动 commit（Aider "编辑前
    dirty commit"），保证 undo 永远能回到干净状态。快照只 add 工作目录子树（git add -A .），
    不把仓库其它部分卷进来。
  - undo(n)：只允许回滚"本会话 agent 自己打的 commit"（记在 .chisel/session_commits.json
    账本），且要求 HEAD 必须等于账本栈顶才允许 —— 保护用户提交不被误伤。
    回滚用 Aider 同款「checkout HEAD~1 -- 受影响文件 + git reset --soft HEAD~1」，
    只还原该 commit 动过的文件，不碰用户其它未提交的工作。
  - 内部直接用 git CLI（subprocess），不走用户 bash 的危险命令确认通道
    （reset --hard 等属于内部回滚，不应触发交互确认）。

5 项优化全部实现：
  1. 快照名带任务上下文
  2. undo 预览（preview_undo）
  3. 已推送保护（检查 commit 是否已 push）
  4. diff 增强（支持 context 行数）
  5. 自动回滚（被 loop_guard 调用）
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools import register_tool

GIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git",
        "description": "Perform Git operations on the workspace repository. "
                       "status: show the working tree. "
                       "commit: stage all changes and commit with a message. "
                       "diff: show unstaged and staged diffs. "
                       "preview_undo: preview what files would be reverted by undo. "
                       "undo: safely revert the last N agent-generated commits.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "commit", "diff", "preview_undo", "undo"]},
                "message": {"type": "string", "description": "action=commit 时的提交信息"},
                "path": {"type": "string", "description": "action=diff 时限定文件"},
                "n": {"type": "integer", "description": "action=undo/preview_undo 时回滚步数，默认 1"},
                "context": {"type": "integer", "description": "action=diff 时上下文行数，默认 3"},
            },
            "required": ["action"],
        },
    },
}


class GitOps:
    def __init__(self, workspace: str):
        self.workspace = workspace
        self.ledger_path = Path(workspace) / ".chisel" / "session_commits.json"

    # --- 基础 --------------------------------------------------------------

    def _git(self, *args: str, cwd: str | None = None) -> tuple[int, str, str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def is_repo(self) -> bool:
        code, _, _ = self._git("rev-parse", "--is-inside-work-tree")
        return code == 0

    # --- 账本 --------------------------------------------------------------

    def _load_ledger(self) -> list[str]:
        if not self.ledger_path.exists():
            return []
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_ledger(self, ledger: list[str]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record(self, sha: str) -> None:
        ledger = self._load_ledger()
        ledger.append(sha)
        self._save_ledger(ledger)

    # --- 第 4 项：已推送检查 ------------------------------------------------

    def _is_pushed(self, sha: str) -> bool:
        """检查 commit 是否已推送到远程。"""
        # git branch -r --contains <sha> 返回包含该 commit 的远程分支
        code, out, _ = self._git("branch", "-r", "--contains", sha)
        if code == 0 and out.strip():
            return True
        # 没有远程分支时，检查是否有远程 tracking
        code, remotes, _ = self._git("remote")
        if not remotes.strip():
            return False  # 没有配置远程 → 视为未推送
        return False

    # --- 对外工具 ----------------------------------------------------------

    def status(self) -> str:
        if not self.is_repo():
            return "Not a git repository."
        code, branch, _ = self._git("branch", "--show-current")
        _, short, _ = self._git("rev-parse", "--short", "HEAD")
        _, porcelain, _ = self._git("status", "--porcelain")
        head = f"{branch or '(detached)'}@{short}"
        if not porcelain:
            return f"Working tree clean ({head})."
        return f"Branch {head}, changes:\n{porcelain}"

    def before_write(self, path: str, task: str = "") -> str:
        """第 1 项：写文件前的版本安全网，快照名带任务上下文。"""
        if not self.is_repo():
            return ""
        code, out, _ = self._git("status", "--porcelain")
        if not code == 0 or not out.strip():
            return ""
        self._git("add", "-A", ".")
        task_prefix = f" [{task[:60]}]" if task else ""
        msg = f"[chisel]{task_prefix} auto-snapshot: {path}"
        rc, out, err = self._git("commit", "-m", msg, "--no-verify")
        if rc == 0:
            _, sha, _ = self._git("rev-parse", "HEAD")
            self._record(sha)
            return f"Auto-snapshot {sha[:8]}: {msg}"
        return err

    def commit(self, message: str) -> str:
        if not self.is_repo():
            return "Not a git repository."
        self._git("add", "-A", ".")
        rc, out, err = self._git("commit", "-m", message, "--no-verify")
        if rc != 0:
            return f"git commit failed: {err or 'no changes to commit'}"
        _, sha, _ = self._git("rev-parse", "HEAD")
        self._record(sha)
        return f"Committed {sha[:8]}: {message}"

    def diff(self, path: str | None = None, context: int = 3) -> str:
        """第 5 项：diff 增强，支持 context 行数。"""
        if not self.is_repo():
            return "Not a git repository."
        args = ["diff", f"-U{context}"]
        if path:
            args.append("--")
            args.append(path)
        _, out, err = self._git(*args)
        if not out:
            args = ["diff", "--cached", f"-U{context}"]
            if path:
                args += ["--", path]
            _, out, err = self._git(*args)
        return out or "(no uncommitted changes)"

    def preview_undo(self, n: int = 1) -> str:
        """第 2 项：预览 undo 将回滚哪些文件。"""
        if not self.is_repo():
            return "Not a git repository."
        ledger = self._load_ledger()
        if not ledger:
            return "No agent commits to undo."
        n = max(1, int(n or 1))
        if n > len(ledger):
            return f"Only {len(ledger)} agent commits, cannot undo {n}."
        for i in range(n):
            expect = ledger[-1 - i]
            _, sha, _ = self._git("rev-parse", f"HEAD~{i}")
            if sha != expect:
                return f"Cannot preview: HEAD~{i} is not an agent commit."
        lines = [f"Preview: undo will revert {n} commit(s):"]
        for i in range(n):
            _, msg, _ = self._git("log", "--format=%s", "-1", f"HEAD~{i}")
            _, files, _ = self._git("show", "--name-only", "--format=", f"HEAD~{i}")
            short = sha[:8] if (_, sha, _ := self._git("rev-parse", f"HEAD~{i}")) else ""
            lines.append(f"  {sha[:8]}: {msg}")
            for f in files.splitlines():
                if f.strip():
                    lines.append(f"    - {f.strip()}")
        return "\n".join(lines)

    def undo(self, n: int = 1) -> str:
        """回滚最近 n 个本会话 agent 的提交。含第 3 项：已推送检查。"""
        if not self.is_repo():
            return "Not a git repository."
        ledger = self._load_ledger()
        if not ledger:
            return "No agent commits to undo."
        n = max(1, int(n or 1))
        if n > len(ledger):
            return f"Only {len(ledger)} agent commits, cannot undo {n}."
        # 校验 HEAD 与账本一致
        for i in range(n):
            expect = ledger[-1 - i]
            _, sha, _ = self._git("rev-parse", f"HEAD~{i}")
            if sha != expect:
                return (
                    f"Cannot undo: HEAD~{i} ({sha[:8]}) is not an agent commit "
                    f"(ledger has {expect[:8]}). Your commits are protected."
                )
        # 第 3 项：已推送检查
        for i in range(n):
            _, sha, _ = self._git("rev-parse", f"HEAD~{i}")
            if self._is_pushed(sha):
                return (
                    f"Cannot undo: commit {sha[:8]} has already been pushed to remote. "
                    f"Revert manually with git revert."
                )
        # 执行回滚
        rolled = []
        for _ in range(n):
            _, files, _ = self._git("show", "--name-only", "--format=", "HEAD")
            _, parent, _ = self._git("rev-parse", "HEAD~1")
            for f in files.splitlines():
                f = f.strip()
                if not f:
                    continue
                code, _, _ = self._git("cat-file", "-e", f"{parent}:{f}")
                if code == 0:
                    self._git("checkout", parent, "--", f)
                else:
                    self._git("rm", "-f", f)
            self._git("reset", "--soft", parent)
            _, sha, _ = self._git("rev-parse", "HEAD")
            rolled.append(sha[:8])
            ledger.pop()
        self._save_ledger(ledger)
        return f"Rolled back {n} commit(s): {', '.join(rolled)}. Files reverted, other work untouched."


def _handle_git(ctx, args: dict) -> str:
    g = ctx.ensure_git()
    action = args.get("action")
    if action == "status":
        return g.status()
    if action == "commit":
        msg = args.get("message", "")
        return g.commit(msg or "commit")
    if action == "diff":
        return g.diff(args.get("path"), args.get("context") or 3)
    if action == "preview_undo":
        return g.preview_undo(args.get("n") or 1)
    if action == "undo":
        return g.undo(args.get("n") or 1)
    return f"git: unknown action {action!r}, expected status/commit/diff/preview_undo/undo"


register_tool(GIT_SCHEMA, _handle_git)