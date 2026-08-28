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
        "description": "Git 操作。status 查看改动；diff 查看未提交的改动（可限定文件）；"
                       "commit 用给定 message 提交；undo 撤销最近 N 次本会话产生的提交（安全回滚，"
                       "不会动用户自己的提交）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "commit", "diff", "undo"]},
                "message": {"type": "string", "description": "action=commit 时的提交信息"},
                "path": {"type": "string", "description": "action=diff 时限定文件"},
                "n": {"type": "integer", "description": "action=undo 时回滚步数，默认 1"},
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

    # --- 对外工具 ----------------------------------------------------------

    def status(self) -> str:
        if not self.is_repo():
            return "当前目录不是 git 仓库（跳过 git 操作）。"
        code, branch, _ = self._git("branch", "--show-current")
        _, short, _ = self._git("rev-parse", "--short", "HEAD")
        _, porcelain, _ = self._git("status", "--porcelain")
        head = f"{branch or '(detached)'}@{short}"
        if not porcelain:
            return f"工作树干净（{head}）。"
        return f"分支 {head}，改动如下：\n{porcelain}"

    def before_write(self, path: str) -> str:
        """写文件前的版本安全网：工作树脏则自动快照 commit。"""
        if not self.is_repo():
            return ""
        code, out, _ = self._git("status", "--porcelain")
        if not code == 0 or not out.strip():
            return ""  # 无改动，不产生空提交
        # 只 add 工作目录子树，避免把仓库其它部分卷进快照
        self._git("add", "-A", ".")
        rc, out, err = self._git("commit", "-m", f"[chisel] auto-snapshot before edit: {path}", "--no-verify")
        if rc == 0:
            _, sha, _ = self._git("rev-parse", "HEAD")
            self._record(sha)
            return f"（已自动快照提交 {sha[:8]}，可随时 undo 回滚）"
        return err

    def commit(self, message: str) -> str:
        if not self.is_repo():
            return "当前目录不是 git 仓库。"
        self._git("add", "-A", ".")
        rc, out, err = self._git("commit", "-m", message, "--no-verify")
        if rc != 0:
            return f"git commit 失败：{err or '无改动可提交'}"
        _, sha, _ = self._git("rev-parse", "HEAD")
        self._record(sha)
        return f"已提交 {sha[:8]}：{message}"

    def diff(self, path: str | None = None) -> str:
        if not self.is_repo():
            return "当前目录不是 git 仓库。"
        args = ["diff"]
        if path:
            args.append("--")
            args.append(path)
        _, out, err = self._git(*args)
        if not out:
            # 试试暂存区
            args = ["diff", "--cached"]
            if path:
                args += ["--", path]
            _, out, err = self._git(*args)
        return out or "(无未提交改动)"

    def undo(self, n: int = 1) -> str:
        """回滚最近 n 个本会话 agent 的提交。HEAD 非账本栈顶时拒绝。"""
        if not self.is_repo():
            return "当前目录不是 git 仓库。"
        ledger = self._load_ledger()
        if not ledger:
            return "没有可回滚的本会话提交（本会话 agent 尚未提交过）。"
        n = max(1, int(n or 1))
        if n > len(ledger):
            return f"回滚失败：本会话只有 {len(ledger)} 个提交，无法回滚 {n} 个。"
        _, head, _ = self._git("rev-parse", "HEAD")
        # 校验最近 n 个 HEAD 与账本栈顶一致，防止误回滚用户提交
        for i in range(n):
            expect = ledger[-1 - i]
            _, sha, _ = self._git("rev-parse", f"HEAD~{i}")
            if sha != expect:
                return (
                    f"拒绝回滚：HEAD~{i}（{sha[:8]}）不是本会话的提交（账本记录 {expect[:8]}）。"
                    f"为避免误伤你的提交，请手动处理。"
                )
        # Aider 式回滚：只还原该 commit 动过的文件，不碰其它未提交工作
        rolled = []
        for _ in range(n):
            _, files, _ = self._git("show", "--name-only", "--format=", "HEAD")
            _, parent, _ = self._git("rev-parse", "HEAD~1")
            for f in files.splitlines():
                f = f.strip()
                if not f:
                    continue
                # parent 里有该文件 → checkout 还原；没有（本次提交新建）→ 删除
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
        return f"已回滚 {n} 个提交（{', '.join(rolled)}）。文件已恢复到上一个状态，其它未提交工作未受影响。"


def _handle_git(ctx, args: dict) -> str:
    g = ctx.ensure_git()
    action = args.get("action")
    if action == "status":
        return g.status()
    if action == "commit":
        msg = args.get("message", "")
        return g.commit(msg or "[chisel] 提交")
    if action == "diff":
        return g.diff(args.get("path"))
    if action == "undo":
        return g.undo(args.get("n") or 1)
    return f"git 失败：未知 action {action!r}，应为 status/commit/diff/undo"


register_tool(GIT_SCHEMA, _handle_git)
