"""安全分析器：风险等级评估 + 用户确认 + 会话级 always_allow 缓存 + LLM 分析 + 审计日志。

改进：
  - LLM 辅助风险分析：用另一个 LLM 调用评估命令风险
  - Shell 语义分析（AST）：tree-sitter-bash 解析命令结构
  - 操作审计日志：所有确认/拒绝操作记录到 .chisel/audit.log
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.confirmation_policy import ConfirmationPolicy, ConfirmRisky
from core.security_risk import SecurityRisk
from core.shell_semantics import analyze_command

RISK_PATTERNS: list[tuple[re.Pattern, SecurityRisk, str]] = [
    (re.compile(r"\brm\s+-rf?\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files or directories (irreversible)"),
    (re.compile(r"\brm\s+-r\s", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete a directory"),
    (re.compile(r"\bsudo\b", re.IGNORECASE), SecurityRisk.HIGH, "Execute command with superuser privileges"),
    (re.compile(r"\bchmod\s+-R\s*777\b", re.IGNORECASE), SecurityRisk.HIGH, "Make all files world-writable"),
    (re.compile(r"\bcurl\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script"),
    (re.compile(r"\bwget\b.*-O\b", re.IGNORECASE), SecurityRisk.HIGH, "Download file to writable location"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b", re.IGNORECASE), SecurityRisk.HIGH, "Shut down or restart the system"),
    (re.compile(r"\bdd\s+if=", re.IGNORECASE), SecurityRisk.HIGH, "Write directly to a block device"),
    (re.compile(r">\s*/dev/(sd|hd|nvme|mmcblk|xvd|vd|disk)", re.IGNORECASE), SecurityRisk.HIGH, "Write directly to disk device"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), SecurityRisk.HIGH, "Format a drive"),
    (re.compile(r"\b(mkfs|fdisk|parted)\b", re.IGNORECASE), SecurityRisk.HIGH, "Create or modify disk partitions"),
    (re.compile(r"\bgit\s+push\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Push to remote repository"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Hard reset git history"),
    (re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Drop database table or database"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete all rows from a table"),
    (re.compile(r"\b(eval|exec)\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Execute arbitrary code"),
    (re.compile(r"\bpip\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install Python package (supply chain risk)"),
    (re.compile(r"\bnpm\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install npm package (supply chain risk)"),
    (re.compile(r"\b:\(\)\{\s*:\|:\&\s*\}\s*;\s*:", re.IGNORECASE), SecurityRisk.MEDIUM, "Fork bomb"),
    (re.compile(r"\brd\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete directory (Windows)"),
    (re.compile(r"\bdel\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files (Windows)"),
    (re.compile(r"Remove-Item\b.*-(Recurse|Force)", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete (PowerShell)"),
]


class SecurityAnalyzer:
    """安全分析器，每个会话一个实例。"""

    def __init__(self, interactive: bool, policy: ConfirmationPolicy | None = None, client=None):
        self.interactive = interactive
        self.policy = policy or ConfirmRisky()
        self.client = client  # 用于 LLM 辅助分析
        self.always_allow: dict[str, bool] = {}
        self._audit_log: list[dict] = []  # 审计日志

    def _command_hash(self, command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]

    def assess(self, command: str) -> tuple[SecurityRisk, str]:
        """评估命令风险。先用 shell 语义分析，再用正则，最后用 LLM。"""
        # 1) Shell 语义分析（AST）
        try:
            risk, reason = analyze_command(command)
            if risk == "HIGH":
                return SecurityRisk.HIGH, reason
            if risk == "MEDIUM":
                return SecurityRisk.MEDIUM, reason
        except Exception:
            pass

        # 2) 正则匹配
        for pattern, risk, description in RISK_PATTERNS:
            if pattern.search(command):
                return risk, description

        # 3) LLM 辅助分析（可选）
        if self.client and len(command) > 20:
            try:
                llm_risk, llm_reason = self._llm_assess(command)
                if llm_risk == "HIGH":
                    return SecurityRisk.HIGH, llm_reason
                if llm_risk == "MEDIUM":
                    return SecurityRisk.MEDIUM, llm_reason
            except Exception:
                pass

        return SecurityRisk.LOW, ""

    def _llm_assess(self, command: str) -> tuple[str, str]:
        """用 LLM 评估命令风险。"""
        resp = self.client.chat(
            [
                {"role": "system", "content": "You are a security analyzer. Assess the risk of this shell command. "
                                               "Reply with exactly one line: HIGH|MEDIUM|LOW followed by a brief reason."},
                {"role": "user", "content": command},
            ]
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("HIGH"):
            return ("HIGH", text[5:].strip())
        if text.startswith("MEDIUM"):
            return ("MEDIUM", text[7:].strip())
        return ("LOW", "")

    def check(self, command: str) -> bool:
        """检查命令是否允许执行。返回 True=放行，False=拦截。"""
        cmd_hash = self._command_hash(command)

        if cmd_hash in self.always_allow:
            self._audit("allow", command, "always_allow_cache")
            return True

        risk, description = self.assess(command)

        if not self.policy.should_confirm(risk):
            self._audit("allow", command, f"risk={risk.value}")
            return True

        if not self.interactive:
            print(f"\n  ⛔  Blocked: {command}", flush=True)
            print(f"     Risk: {risk.value} - {description}", flush=True)
            print(f"     (Non-interactive mode, auto-rejected)", flush=True)
            self._audit("reject", command, f"risk={risk.value}, non-interactive")
            return False

        print(f"\n  ⚠️  {risk.value} Risk Operation", flush=True)
        print(f"  Command: {command}", flush=True)
        if description:
            print(f"  Reason: {description}", flush=True)
        try:
            ans = input("  Confirm? (y/N/a) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("  (Input interrupted, rejected)", flush=True)
            self._audit("reject", command, "input_interrupted")
            return False

        if ans == "a":
            self.always_allow[cmd_hash] = True
            print(f"  → Allowed for this session", flush=True)
            self._audit("allow", command, f"risk={risk.value}, always")
            return True
        if ans in ("y", "yes"):
            self._audit("allow", command, f"risk={risk.value}")
            return True
        self._audit("reject", command, f"risk={risk.value}")
        return False

    def _audit(self, action: str, command: str, reason: str) -> None:
        """记录审计日志。"""
        self._audit_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "command": command[:200],
            "reason": reason,
        })

    def persist_audit(self, workspace: str) -> None:
        """持久化审计日志到 .chisel/audit.log。"""
        if not self._audit_log:
            return
        path = Path(workspace) / ".chisel" / "audit.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 以 JSONL 格式追加
        with open(path, "a", encoding="utf-8") as f:
            for entry in self._audit_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._audit_log = []