"""安全分析器：风险等级评估 + 用户确认 + 会话级 always_allow 缓存。

架构参考 OpenHands 多层安全防御：
  1. always_allow 缓存：用户标记"本次会话不再问"的命令
  2. 风险等级评估（正则 → SecurityRisk）
  3. 策略引擎（按等级决定是否确认）
  4. 用户确认 prompt（含风险说明 + always 选项）
"""
from __future__ import annotations

import hashlib
import re
import sys

from core.confirmation_policy import ConfirmationPolicy, ConfirmRisky
from core.security_risk import SecurityRisk

# 风险模式表：每个模式带风险等级和说明
RISK_PATTERNS: list[tuple[re.Pattern, SecurityRisk, str]] = [
    # HIGH 风险
    (re.compile(r"\brm\s+-rf?\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files or directories (irreversible)"),
    (re.compile(r"\brm\s+-r\s", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete a directory"),
    (re.compile(r"\bsudo\b", re.IGNORECASE), SecurityRisk.HIGH, "Execute command with superuser privileges"),
    (re.compile(r"\bchmod\s+-R\s*777\b", re.IGNORECASE), SecurityRisk.HIGH, "Make all files world-writable (security risk)"),
    (re.compile(r"\bcurl\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script"),
    (re.compile(r"\bwget\b.*-O\b", re.IGNORECASE), SecurityRisk.HIGH, "Download file to a writable location"),
    (re.compile(r"\bwget\b.*\|\s*(bash|sh|zsh)\b", re.IGNORECASE), SecurityRisk.HIGH, "Download and execute remote script"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b", re.IGNORECASE), SecurityRisk.HIGH, "Shut down or restart the system"),
    (re.compile(r"\bdd\s+if=", re.IGNORECASE), SecurityRisk.HIGH, "Write directly to a block device"),
    (re.compile(r">\s*/dev/(sd|hd|nvme|mmcblk|xvd|vd|disk)", re.IGNORECASE), SecurityRisk.HIGH, "Write directly to a disk device"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), SecurityRisk.HIGH, "Format a drive"),
    (re.compile(r"\b(mkfs|fdisk|parted)\b", re.IGNORECASE), SecurityRisk.HIGH, "Create or modify disk partitions"),

    # MEDIUM 风险
    (re.compile(r"\bgit\s+push\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Push changes to remote repository"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Hard reset git history"),
    (re.compile(r"\bgit\s+push\s+--force\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Force push (may overwrite remote history)"),
    (re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Drop database table or database"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Delete all rows from a table"),
    (re.compile(r"\bpip\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install Python package (supply chain risk)"),
    (re.compile(r"\bnpm\s+install\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Install npm package (supply chain risk)"),
    (re.compile(r"\beval\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Evaluate arbitrary code"),
    (re.compile(r"\bexec\b", re.IGNORECASE), SecurityRisk.MEDIUM, "Execute arbitrary code"),
    (re.compile(r"\b:\(\)\{\s*:\|:\&\s*\}\s*;\s*:", re.IGNORECASE), SecurityRisk.MEDIUM, "Fork bomb (process exhaustion)"),

    # Windows 特有
    (re.compile(r"\brd\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete directory (Windows)"),
    (re.compile(r"\bdel\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete files (Windows)"),
    (re.compile(r"\brmdir\s+/s\b", re.IGNORECASE), SecurityRisk.HIGH, "Recursively remove directory (Windows)"),
    (re.compile(r"Remove-Item\b.*-(Recurse|Force)", re.IGNORECASE), SecurityRisk.HIGH, "Recursively delete (PowerShell)"),
]


class SecurityAnalyzer:
    """安全分析器，每个会话一个实例。"""

    def __init__(self, interactive: bool, policy: ConfirmationPolicy | None = None):
        self.interactive = interactive
        self.policy = policy or ConfirmRisky()
        self.always_allow: dict[str, bool] = {}  # 命令 hash → True

    def _command_hash(self, command: str) -> str:
        """对命令取 hash，用于 always_allow 缓存。"""
        return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]

    def assess(self, command: str) -> tuple[SecurityRisk, str]:
        """评估命令的风险等级。返回 (风险等级, 说明)。"""
        for pattern, risk, description in RISK_PATTERNS:
            if pattern.search(command):
                return risk, description
        return SecurityRisk.LOW, ""

    def check(self, command: str) -> bool:
        """检查命令是否允许执行。返回 True=放行，False=拦截。"""
        cmd_hash = self._command_hash(command)

        # 1. always_allow 缓存
        if cmd_hash in self.always_allow:
            return True

        # 2. 评估风险
        risk, description = self.assess(command)

        # 3. 低风险 → 自动放行
        if not self.policy.should_confirm(risk):
            return True

        # 4. 非交互环境 → 默认拒绝
        if not self.interactive:
            print(f"\n  ⛔  Blocked: {command}", flush=True)
            print(f"     Risk: {risk.value} - {description}", flush=True)
            print(f"     (Non-interactive mode, auto-rejected)", flush=True)
            return False

        # 5. 用户确认
        print(f"\n  ⚠️  {risk.value} Risk Operation", flush=True)
        print(f"  Command: {command}", flush=True)
        if description:
            print(f"  Reason: {description}", flush=True)
        try:
            ans = input("  Confirm? (y/N/a) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("  (Input interrupted, rejected)", flush=True)
            return False

        if ans == "a":
            # 本次会话不再问
            self.always_allow[cmd_hash] = True
            print(f"  → Allowed for this session", flush=True)
            return True
        if ans in ("y", "yes"):
            return True
        return False