"""安全分析器：四级风险评估 + 用户确认 + 会话缓存 + LLM 分析 + 审计日志 + Dry-Run 预览。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from core.confirmation_policy import ConfirmationPolicy, ConfirmRisky
from core.security_risk import SecurityRisk
from core.shell_semantics import analyze_command

RISK_PATTERNS: list[tuple[re.Pattern, SecurityRisk, str, str]] = [
    # CRITICAL
    (re.compile(r"\brm\s+-rf?\s+(/etc\b|/bin\b|/boot\b|/dev\b|/home\b|/root\b|/var\b|/usr\b|/lib\b|/proc\b|/sys\b)", re.IGNORECASE), SecurityRisk.CRITICAL, "Recursive delete on system directory", "catastrophic data loss"),
    (re.compile(r"\brm\s+-rf?\s+\.git", re.IGNORECASE), SecurityRisk.CRITICAL, "Recursive delete on .git directory", "irreversible git history loss"),
    (re.compile(r"\brm\s+-rf?\s+\.", re.IGNORECASE), SecurityRisk.HIGH, "Recursive delete in current directory", "may delete project files"),
    (re.compile(r"\brm\s+-rf?\s+~", re.IGNORECASE), SecurityRisk.CRITICAL, "Recursive delete on home directory", "catastrophic data loss"),
    (re.compile(r"\bdd\s+if=", re.IGNORECASE), SecurityRisk.HIGH, "Write directly to a block device", "irreversible disk damage"),
    (re.compile(r">\s*/dev/(sd|hd|nvme|mmcblk|xvd|vd|disk)", re.IGNORECASE), SecurityRisk.CRITICAL, "Write directly to disk device", "irreversible disk damage"),
    (re.compile(r"\b(find\s+.*\s+-delete)\b", re.IGNORECASE), SecurityRisk.HIGH, "Bulk file deletion via find -delete", "may delete many files at once"),
    (re.compile(r"\b(find\s+.*\s+-exec\s*rm)\b", re.IGNORECASE), SecurityRisk.HIGH, "Bulk file deletion via find exec rm", "may delete many files at once"),
    (re.compile(r"\b(mkfs|fdisk|parted)\b", re.IGNORECASE), SecurityRisk.HIGH, "Create or modify disk partitions", "irreversible disk changes"),
    # MEDIUM patterns before general HIGH rm -rf (specific patterns must come first)
    (re.compile(r"\brm\s+-rf?\s+.*__pycache__", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete __pycache__ directories", "cache cleanup"),
    (re.compile(r"\brm\s+-rf?\s+.*\.pytest_cache", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete .pytest_cache directories", "cache cleanup"),
    (re.compile(r"\brm\s+-rf?\s+.*\.pyc", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete .pyc files", "cache cleanup"),
    (re.compile(r"\brm\s+-rf?\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files or directories", "irreversible data loss"),
    (re.compile(r"\brm\s+-r\s", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete a directory", "data loss"),
    # HIGH
    (re.compile(r"\bsudo\b", re.IGNORECASE), SecurityRisk.HIGH, "Execute command with superuser privileges", "privilege escalation"),
    (re.compile(r"\bchmod\s+-R\s*777\b", re.IGNORECASE), SecurityRisk.HIGH, "Make all files world-writable", "security vulnerability"),
    (re.compile(r"\bcurl\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script", "remote code execution"),
    (re.compile(r"\bwget\b.*-O\b", re.IGNORECASE), SecurityRisk.HIGH, "Download file to a writable location", "file overwrite"),
    (re.compile(r"\bwget\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script", "remote code execution"),
    (re.compile(r"\bwget\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script", "remote code execution"),
    (re.compile(r"\b(shutdown|reboot|poweroff)\b", re.IGNORECASE), SecurityRisk.HIGH, "Shut down or restart the system", "service interruption"),
    # MEDIUM
    (re.compile(r"\brm\s+-rf?\s+.*__pycache__", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete __pycache__ directories", "cache cleanup"),
    (re.compile(r"\brm\s+-rf?\s+.*\.pytest_cache", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete .pytest_cache directories", "cache cleanup"),
    (re.compile(r"\brm\s+-rf?\s+.*\.pyc", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete .pyc files", "cache cleanup"),
    (re.compile(r"\bgit\s+push\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Push to remote repository", "remote changes"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Hard reset git history", "may lose uncommitted changes"),
    (re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Drop database table or database", "data loss"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete all rows from a table", "data loss"),
    (re.compile(r"\b(eval|exec)\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Execute arbitrary code", "code injection"),
    (re.compile(r"\bpip\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install Python package", "supply chain risk"),
    (re.compile(r"\bnpm\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install npm package", "supply chain risk"),
    # Windows
    (re.compile(r"\brd\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete directory (Windows)", "data loss"),
    (re.compile(r"\bdel\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files (Windows)", "data loss"),
    (re.compile(r"Remove-Item\b.*-(Recurse|Force)", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete (PowerShell)", "data loss"),
]

# 受保护路径模式
_PROTECTED_PATTERNS = [
    (r"\.git[/\\]", ".git directory", "irreversible git history loss"),
    (r"\.env[^.]*", ".env file", "API key and credential loss"),
    (r"\.vscode[/\\]", ".vscode directory", "IDE configuration loss"),
    (r"\.ssh[/\\]", ".ssh directory", "SSH key loss"),
    (r"\.gitignore", ".gitignore file", "git ignore rules loss"),
]


class SecurityAnalyzer:
    def __init__(self, interactive: bool, policy: ConfirmationPolicy | None = None, client=None, workspace: str = ""):
        self.interactive = interactive
        self.policy = policy or ConfirmRisky()
        self.client = client
        self.workspace = workspace
        self.always_allow: dict[str, bool] = {}
        self._audit_log: list[dict] = []

    def _command_hash(self, command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]

    def assess(self, command: str) -> tuple[SecurityRisk, str, str]:
        """评估命令风险。返回 (risk_level, reason, consequence)。"""
        # 1) 检查受保护路径
        for pat, reason, consequence in _PROTECTED_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return SecurityRisk.CRITICAL, f"Targets protected resource: {reason}", consequence

        # 2) Shell 语义分析（AST）
        try:
            risk, reason = analyze_command(command)
            if risk == "CRITICAL":
                return SecurityRisk.CRITICAL, reason, "catastrophic data loss"
            if risk == "HIGH":
                return SecurityRisk.HIGH, reason, "significant data loss"
            if risk == "MEDIUM":
                return SecurityRisk.MEDIUM, reason, "moderate impact"
        except Exception:
            pass

        # 3) 正则匹配
        for pattern, risk, reason, consequence in RISK_PATTERNS:
            if pattern.search(command):
                return risk, reason, consequence

        # 4) LLM 辅助分析（可选）
        if self.client and len(command) > 20:
            try:
                llm_risk, llm_reason = self._llm_assess(command)
                if llm_risk == "CRITICAL":
                    return SecurityRisk.CRITICAL, llm_reason, "catastrophic data loss"
                if llm_risk == "HIGH":
                    return SecurityRisk.HIGH, llm_reason, "significant impact"
                if llm_risk == "MEDIUM":
                    return SecurityRisk.MEDIUM, llm_reason, "moderate impact"
            except Exception:
                pass

        return SecurityRisk.LOW, "", ""

    def _llm_assess(self, command: str) -> tuple[str, str]:
        resp = self.client.chat([
            {"role": "system", "content": "You are a security analyzer. Assess the risk of this shell command. Reply with exactly one line: CRITICAL|HIGH|MEDIUM|LOW followed by a brief reason."},
            {"role": "user", "content": command},
        ])
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("CRITICAL"):
            return ("CRITICAL", text[9:].strip())
        if text.startswith("HIGH"):
            return ("HIGH", text[5:].strip())
        if text.startswith("MEDIUM"):
            return ("MEDIUM", text[7:].strip())
        return ("LOW", "")

    def dry_run(self, command: str) -> list[dict]:
        """执行 Dry-Run 预览，返回拟删除/修改的文件列表。"""
        items = []
        workspace = os.getcwd()
        # 尝试解析命令中的路径模式
        for pat, reason, consequence in _PROTECTED_PATTERNS:
            m = re.search(pat, command, re.IGNORECASE)
            if m:
                matched = m.group(0)
                full_path = None
                for root, dirs, files in os.walk(workspace):
                    for name in dirs + files:
                        if re.search(pat, name):
                            full_path = os.path.join(root, name)
                            break
                    if full_path:
                        break
                items.append({
                    "path": full_path or matched,
                    "risk": "CRITICAL",
                    "reason": reason,
                    "consequence": consequence,
                    "size": os.path.getsize(full_path) if full_path and os.path.isfile(full_path) else 0,
                })

        # 检查 rm -rf 相关
        if "rm -rf" in command or "rm -r" in command:
            for root, dirs, files in os.walk(workspace):
                for name in dirs + files:
                    full = os.path.join(root, name)
                    risk = "MEDIUM"
                    for pat, reason, _ in _PROTECTED_PATTERNS:
                        if re.search(pat, name):
                            risk = "CRITICAL"
                            break
                    if name in ("__pycache__", ".pytest_cache") or name.endswith(".pyc"):
                        risk = "MEDIUM"
                    items.append({
                        "path": full,
                        "risk": risk,
                        "size": os.path.getsize(full) if os.path.isfile(full) else 0,
                    })
                    if len(items) > 100:
                        break
                if len(items) > 100:
                    break

        return items

    def check(self, command: str) -> bool:
        cmd_hash = self._command_hash(command)

        if cmd_hash in self.always_allow:
            self._audit("allow", command, "always_allow_cache")
            return True

        risk, reason, consequence = self.assess(command)

        if not self.policy.should_confirm(risk):
            self._audit("allow", command, f"risk={risk.value}")
            return True

        if not self.interactive:
            self._print_blocked(command, risk, reason)
            self._audit("reject", command, f"risk={risk.value}, non-interactive")
            return False

        # CRITICAL 风险：Dry-Run 预览 + 强确认
        if risk == SecurityRisk.CRITICAL:
            return self._confirm_critical(command, risk, reason, consequence)

        # HIGH 风险：Dry-Run 预览 + 标准确认
        if risk == SecurityRisk.HIGH:
            return self._confirm_high(command, risk, reason, consequence)

        # MEDIUM 风险：标准确认
        return self._confirm_medium(command, risk, reason)

    def _print_blocked(self, command, risk, reason=""):
        print(f"\n  ⛔  Blocked: {command}", flush=True)
        print(f"     Risk: {risk.value} - {reason}", flush=True)
        print(f"     (Non-interactive mode, auto-rejected)", flush=True)

    def _confirm_critical(self, command, risk, reason, consequence):
        """CRITICAL 风险：Dry-Run 预览 + 强确认。"""
        # 桌面模式：IPC 按钮
        if os.environ.get("CHISEL_DESKTOP") and self.workspace:
            from core.user_input import ask_question
            preview = self.dry_run(command)
            msg = f"💀 高危操作\n命令：{command}\n说明：{reason}\n后果：{consequence}"
            if preview:
                msg += f"\n\n受影响文件：{len(preview)} 项"
                crit = [i for i in preview if i.get("risk") == "CRITICAL"]
                for i in crit[:5]:
                    msg += f"\n  💀 {i['path']}"
            protected = []
            for pat, reason_p, _ in _PROTECTED_PATTERNS:
                if re.search(pat, command, re.IGNORECASE):
                    protected.append(pat)
            if protected:
                ans = ask_question(self.workspace, msg + "\n\n⚠️ 涉及受保护资源，请确认", ["确认执行", "取消"])
                if ans == "确认执行":
                    self._audit("allow", command, f"risk={risk.value}, ipc_confirm")
                    return True
                self._audit("reject", command, f"risk={risk.value}, ipc_reject")
                return False
            ans = ask_question(self.workspace, msg, ["是", "否"])
            if ans == "是":
                self._audit("allow", command, f"risk={risk.value}, ipc_confirm")
                return True
            self._audit("reject", command, f"risk={risk.value}, ipc_reject")
            return False

        # CLI 模式
        print(f"\n  {'='*50}", flush=True)
        print(f"  💀  CRITICAL Risk Operation", flush=True)
        print(f"  {'='*50}", flush=True)
        print(f"  Command: {command}")
        print(f"  Reason: {reason}")
        print(f"  Consequence: {consequence}")
        print()
        preview = self.dry_run(command)
        if preview:
            crit = [i for i in preview if i.get("risk") == "CRITICAL"]
            high = [i for i in preview if i.get("risk") == "HIGH"]
            medium = [i for i in preview if i.get("risk") == "MEDIUM"]
            print(f"  Dry-Run Preview ({len(preview)} items affected):")
            for i in crit[:5]:
                print(f"    💀 [CRITICAL] {i['path']}")
            for i in high[:5]:
                print(f"    🔴 [HIGH]     {i['path']}")
            for i in medium[:5]:
                print(f"    ⚠️  [MEDIUM]   {i['path']}")
            print()
        protected = []
        for pat, reason_p, _ in _PROTECTED_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                protected.append(pat)
        if protected:
            print(f"  ⚠️  This operation targets protected resources: {', '.join(protected)}")
            phrase = f"CONFIRM DELETE {Path(command.split()[-1]).name if command.split() else 'FILES'}"
            ans = input(f"  Type confirmation phrase to proceed:\n  > ").strip()
            if ans == phrase:
                self._audit("allow", command, f"risk={risk.value}, phrase_confirmed")
                return True
            self._audit("reject", command, f"risk={risk.value}, phrase_mismatch")
            return False
        ans = input(f"  Confirm? (y/N) ").strip().lower()
        if ans in ("y", "yes"):
            self._audit("allow", command, f"risk={risk.value}")
            return True
        self._audit("reject", command, f"risk={risk.value}")
        return False

    def _confirm_high(self, command, risk, reason, consequence):
        """HIGH 风险：Dry-Run 预览 + 标准确认。"""
        if os.environ.get("CHISEL_DESKTOP") and self.workspace:
            from core.user_input import ask_question
            msg = f"🔴 高危操作\n命令：{command}\n说明：{reason}"
            preview = self.dry_run(command)
            if preview:
                msg += f"\n\n受影响文件：{len(preview)} 项"
                for i in preview[:5]:
                    msg += f"\n  - {i['path']}"
            ans = ask_question(self.workspace, msg, ["是", "否（本次会话不再询问）", "否"])
            if ans == "是":
                self._audit("allow", command, f"risk={risk.value}, ipc_confirm")
                return True
            if ans == "否（本次会话不再询问）":
                self._audit("reject", command, f"risk={risk.value}, ipc_reject")
                return False
            self._audit("reject", command, f"risk={risk.value}, ipc_reject")
            return False

        print(f"\n  🔴  HIGH Risk Operation", flush=True)
        print(f"  Command: {command}")
        print(f"  Reason: {reason}")
        preview = self.dry_run(command)
        if preview:
            print(f"  Dry-Run: {len(preview)} items would be affected")
            for i in preview[:10]:
                print(f"    - {i['path']}")
            if len(preview) > 10:
                print(f"    ... and {len(preview)-10} more")
        try:
            ans = input("  Confirm? (y/N/a) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._audit("reject", command, "input_interrupted")
            return False
        if ans == "a":
            self.always_allow[self._command_hash(command)] = True
            self._audit("allow", command, f"risk={risk.value}, always")
            return True
        if ans in ("y", "yes"):
            self._audit("allow", command, f"risk={risk.value}")
            return True
        self._audit("reject", command, f"risk={risk.value}")
        return False

    def _confirm_medium(self, command, risk, reason):
        """MEDIUM 风险：标准确认。"""
        if os.environ.get("CHISEL_DESKTOP") and self.workspace:
            from core.user_input import ask_question
            msg = f"⚠️ 中风险操作\n命令：{command}"
            if reason:
                msg += f"\n说明：{reason}"
            ans = ask_question(self.workspace, msg, ["是", "否（本次会话不再询问）", "否"])
            if ans == "是":
                self._audit("allow", command, f"risk={risk.value}, ipc_confirm")
                return True
            if ans == "否（本次会话不再询问）":
                self._audit("reject", command, f"risk={risk.value}, ipc_reject")
                return False
            self._audit("reject", command, f"risk={risk.value}, ipc_reject")
            return False

        print(f"\n  ⚠️  {risk.value} Risk Operation", flush=True)
        print(f"  Command: {command}", flush=True)
        if reason:
            print(f"  Reason: {reason}", flush=True)
        try:
            ans = input("  Confirm? (y/N/a) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._audit("reject", command, "input_interrupted")
            return False
        if ans == "a":
            self.always_allow[self._command_hash(command)] = True
            self._audit("allow", command, f"risk={risk.value}, always")
            return True
        if ans in ("y", "yes"):
            self._audit("allow", command, f"risk={risk.value}")
            return True
        self._audit("reject", command, f"risk={risk.value}")
        return False

    def _audit(self, action: str, command: str, reason: str) -> None:
        self._audit_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "command": command[:200],
            "reason": reason,
        })

    def persist_audit(self, workspace: str) -> None:
        if not self._audit_log:
            return
        path = Path(workspace) / ".chisel" / "audit.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for entry in self._audit_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._audit_log = []