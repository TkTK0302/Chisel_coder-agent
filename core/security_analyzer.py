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
    # MEDIUM —— 条件命令的写操作变体
    (re.compile(r"\bsed\s+.*(?<!\w)-i\b", re.IGNORECASE), SecurityRisk.MEDIUM, "sed -i modifies file in-place", "file modification"),
    (re.compile(r"\bcurl\b.*(?<!\w)-[oO]\b", re.IGNORECASE), SecurityRisk.MEDIUM, "curl writing to file", "file download"),
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
    (r"\.git$", ".git directory", "irreversible git history loss"),
    (r"\.env[^.]*", ".env file", "API key and credential loss"),
    (r"\.pytest_cache[/\\]", ".pytest_cache directory", "test cache"),
    (r"\.pytest_cache$", ".pytest_cache directory", "test cache"),
    (r"\.vscode[/\\]", ".vscode directory", "IDE configuration loss"),
    (r"\.ssh[/\\]", ".ssh directory", "SSH key loss"),
    (r"\.gitignore", ".gitignore file", "git ignore rules loss"),
]


# 绝对安全命令 —— 这些命令在任何情况下都不能修改文件
# 即使引用受保护路径（如 cat .env），也直接放行
_ABSOLUTE_SAFE_CMDS = {
    # 文本查看
    "cat", "head", "tail", "less", "more", "zcat", "bzcat", "xzcat",
    # 搜索
    "grep", "egrep", "fgrep", "rg", "ag",
    # 文件/目录信息
    "ls", "dir", "pwd", "stat", "file", "du", "df", "tree",
    "readlink", "realpath", "basename", "dirname",
    # 查找（不带 -delete/-exec 的 find 在下层条件判断中处理）
    "locate", "which", "whereis", "type",
    # 信息输出
    "echo", "printf", "date", "env", "printenv",
    "whoami", "hostname", "uname", "id", "groups", "logname",
    "uptime", "tty", "arch", "nproc",
    # 目录导航（只改 shell 状态）
    "cd", "pushd", "popd", "dirs",
    # 文本处理
    "wc", "sort", "uniq", "nl", "od", "hexdump", "xxd",
    "column", "paste", "join", "strings", "jq", "yq",
    "expand", "unexpand", "fmt", "fold", "iconv",
    # 比较/校验
    "diff", "cmp", "comm",
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "sum",
    # 计算器
    "bc", "dc", "expr",
    # 系统监控
    "ps", "top", "htop", "free", "vmstat", "iostat", "netstat", "ss", "lsof",
    "pgrep", "pidof", "pmap",
    # 网络诊断
    "ping", "ping6", "traceroute", "tracepath", "nslookup", "dig", "host",
    # Shell 内置
    "sleep", "true", "false", "test", "[", "man", "help", "clear", "history",
    "declare", "typeset", "compgen", "timeout",
    # Git 只读子命令
    "git log", "git show", "git diff", "git status", "git branch", "git tag",
    "git blame", "git config", "git remote", "git ls-files", "git rev-parse",
    "git rev-list", "git stash list", "git describe", "git shortlog", "git reflog",
    # Docker 只读
    "docker ps", "docker images", "docker logs", "docker inspect", "docker stats",
    "docker version", "docker info", "docker network ls", "docker volume ls",
    # 包管理器只读
    "pip list", "pip show", "pip freeze", "pip config",
    "npm list", "npm view", "npm outdated", "npm config",
}

# 条件命令 —— 同一命令名有读写两种形态，需检查参数
# 格式: {命令名: [写操作标志列表]}
_CONDITIONAL_CMDS = {
    "sed": ["-i"],                       # sed -i 是写操作
    "awk": ["-i"],                       # awk -i inplace 是写操作
    "find": ["-delete", "-exec", "-execdir"],  # find 带这些标志是写操作
    "curl": ["-o", "-O"],                # curl -o/-O 写入文件
    "wget": ["-O"],                      # wget -O 写入文件
}


def _split_by_shell_separators(cmd: str) -> list[str]:
    """按 ; 和 & 拆分命令链，但尊重引号内的字符。

    python -c "import requests; print(1+1)" 中的 ; 不会被拆分，
    但 2>&1; echo done 中的 ; 会被正确拆分。
    """
    segments = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == '\\' and (in_single or in_double):
            # 引号内的转义字符，保留
            current.append(ch)
            i += 1
            if i < len(cmd):
                current.append(cmd[i])
        elif ch == ';' and not in_single and not in_double:
            if current:
                segments.append(''.join(current).strip())
                current = []
        elif ch == '&' and not in_single and not in_double:
            # 检查是否是 && （两个连续的 &）
            if i + 1 < len(cmd) and cmd[i + 1] == '&':
                if current:
                    segments.append(''.join(current).strip())
                    current = []
                i += 1  # 跳过第二个 &
            else:
                # 单个 & 是后台运行，不算分隔符
                current.append(ch)
        else:
            current.append(ch)
        i += 1
    if current:
        segments.append(''.join(current).strip())
    return [s for s in segments if s]


def _is_absolutely_safe(command: str) -> bool:
    """检查命令是否绝对安全——在任何情况下都不能修改文件。

    这些命令即使引用受保护路径（如 cat .env）也直接放行，
    因为它们只是读取/显示，不会删除或修改。
    """
    cmd = command.strip()

    # 有输出重定向到文件 → 不是绝对安全（2>/dev/null 是 stderr，无害）
    if re.search(r"[^2]>\s*\S", cmd) or re.search(r">>\s*\S", cmd) or re.search(r"^>\s*\S", cmd):
        return False

    # 命令链 → 逐段检查
    if ";" in cmd or "&&" in cmd:
        segments = _split_by_shell_separators(cmd)
        if len(segments) > 1:
            return all(_is_absolutely_safe(s) for s in segments)

    # 管道 → 逐段检查
    if "|" in cmd:
        return all(_is_absolutely_safe(p.strip()) for p in cmd.split("|"))

    # 提取基础命令名
    first_word = cmd.split()[0] if cmd.split() else ""
    base_cmd = first_word.split("/")[-1] if "/" in first_word else first_word

    # 精确匹配
    if base_cmd in _ABSOLUTE_SAFE_CMDS:
        return True

    # 前缀匹配（如 "git log", "docker ps"）
    for safe_cmd in _ABSOLUTE_SAFE_CMDS:
        if " " in safe_cmd and cmd.startswith(safe_cmd):
            return True

    return False


def _is_readonly_command(command: str) -> bool:
    """检查命令是否只读（包括条件命令——同一命令名有读写两种形态）。

    先检查绝对安全命令，再检查条件命令的参数。
    例如：sed 不带 -i 是只读，find 不带 -delete 是只读。
    """
    # 绝对安全命令直接通过
    if _is_absolutely_safe(command):
        return True

    # 处理命令链和管道
    cmd = command.strip()
    if ";" in cmd or "&&" in cmd:
        segments = _split_by_shell_separators(cmd)
        if len(segments) > 1:
            return all(_is_readonly_command(s) for s in segments)
    if "|" in cmd:
        return all(_is_readonly_command(p.strip()) for p in cmd.split("|"))

    # 提取基础命令名
    first_word = cmd.split()[0] if cmd.split() else ""
    base_cmd = first_word.split("/")[-1] if "/" in first_word else first_word

    if base_cmd not in _CONDITIONAL_CMDS:
        return False

    # 检查是否包含写操作标志
    write_flags = _CONDITIONAL_CMDS[base_cmd]
    for flag in write_flags:
        if re.search(rf"(?<!\w){re.escape(flag)}\b", cmd):
            return False  # 包含写操作标志

    return True  # 条件命令但没有写操作标志 → 只读


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
        """评估命令风险。返回 (risk_level, reason, consequence)。

        检查顺序（确定性优先，LLM 兜底）：
        1. 绝对安全白名单 → 不能修改文件的命令，直接 LOW
        2. 条件命令检查 → sed/find/curl/wget 等，根据参数判断读写
        3. 受保护路径 → 对能修改文件的命令，检查是否目标受保护资源
        4. Shell AST 语义分析 → 管道组合风险、破坏性命令
        5. 正则模式匹配 → 已知危险模式
        6. LLM 辅助分析 → 前面都无法判定时，才调 LLM
        """
        # 1) 绝对安全：这些命令不能修改文件，直接放行
        #    cat .env 在这里被放行，不会走到受保护路径检查
        if _is_absolutely_safe(command):
            return SecurityRisk.LOW, "", ""

        # 2) 条件命令检查 —— find -print 在这里放行，sed -n 在这里放行
        if _is_readonly_command(command):
            return SecurityRisk.LOW, "", ""

        # 3) 受保护路径 —— 仅对能修改文件且非只读的命令检查
        #     rm -rf .env 走到这里 → CRITICAL
        #     必须在 Shell AST 和正则之前，否则 rm -rf . 会先匹配
        for pat, reason, consequence in _PROTECTED_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                return SecurityRisk.CRITICAL, f"Targets protected resource: {reason}", consequence

        # 4) Shell 语义分析（AST）—— 检测管道组合、破坏性命令
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

        # 5) 正则模式匹配 —— 已知危险模式
        for pattern, risk, reason, consequence in RISK_PATTERNS:
            if pattern.search(command):
                return risk, reason, consequence

        # 6) LLM 辅助分析 —— 仅对前面都无法判定的命令
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
        workspace = self.workspace or os.getcwd()
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
        """CRITICAL 风险：分阶段展示（扫描→判定→确认）+ Shadow Backup。"""
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
                    backup_path = self._shadow_backup(command)
                    if backup_path:
                        print(f"\n  [Shadow Backup] 删除前已备份到 {backup_path}", flush=True)
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

        # ---- CLI 模式：分阶段展示 ----

        # Phase 1: 扫描待删除目标
        print(f"\n  {'─'*50}", flush=True)
        print(f"  [Phase 1] 扫描待删除目标...", flush=True)
        targets = self._resolve_targets(command)
        if targets:
            for t in targets:
                full = Path(self.workspace) / t
                if not full.exists():
                    continue
                if full.is_dir():
                    fcount = sum(1 for _ in full.rglob("*") if _.is_file())
                    size = sum(_.stat().st_size for _ in full.rglob("*") if _.is_file())
                    print(f"    📁 {t}/  ({fcount} files, {size:,} bytes)", flush=True)
                else:
                    size = full.stat().st_size if full.exists() else 0
                    print(f"    📄 {t}  ({size:,} bytes)", flush=True)
        else:
            print(f"    (无法解析命令中的文件路径)", flush=True)

        # Phase 2: 安全策略判定
        print(f"\n  [Phase 2] 安全策略判定...", flush=True)
        preview = self.dry_run(command)
        if preview:
            crit = [i for i in preview if i.get("risk") == "CRITICAL"]
            high = [i for i in preview if i.get("risk") == "HIGH"]
            medium = [i for i in preview if i.get("risk") == "MEDIUM"]
            for i in crit[:5]:
                print(f"    💀 [CRITICAL] {i['path']}  — {i.get('reason', '受保护资源')}", flush=True)
            for i in high[:5]:
                print(f"    🔴 [HIGH]     {i['path']}  — {i.get('reason', '')}", flush=True)
            for i in medium[:5]:
                print(f"    ⚠️  [MEDIUM]   {i['path']}  — {i.get('reason', '')}", flush=True)

        protected = []
        for pat, reason_p, _ in _PROTECTED_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                protected.append((pat, reason_p))
        if protected:
            print(f"\n  ⚠️  检测到受保护路径：", flush=True)
            for pat, reason_p in protected:
                print(f"      • {reason_p}", flush=True)
            print(f"  ⚠️  删除受保护资源将导致不可逆的数据丢失！", flush=True)

        # Phase 3: 人机确认
        print(f"\n  [Phase 3] 人机确认", flush=True)
        print(f"  {'─'*50}", flush=True)
        if protected:
            # 生成确认短语（取命令中最后一个路径参数的文件名）
            last_arg = ""
            parts = command.split()
            for p in reversed(parts):
                if not p.startswith("-") and p not in ("rm", "rmdir", "del", "rd"):
                    last_arg = Path(p).name
                    break
            phrase = f"CONFIRM DELETE {last_arg}" if last_arg else "CONFIRM DELETE FILES"
            print(f"  请输入安全确认短语以继续：", flush=True)
            print(f"  > {phrase}", flush=True)
            try:
                ans = input(f"  > ").strip()
            except (EOFError, KeyboardInterrupt):
                self._audit("reject", command, "input_interrupted")
                return False
            if ans == phrase:
                # 确认通过 → 创建 Shadow Backup
                backup_path = self._shadow_backup(command)
                if backup_path:
                    print(f"\n  📦 [Shadow Backup] 删除前已创建快照：", flush=True)
                    print(f"     {backup_path}", flush=True)
                    # 显示备份内容
                    try:
                        import zipfile
                        with zipfile.ZipFile(backup_path, 'r') as zf:
                            names = zf.namelist()
                            total_size = sum(zf.getinfo(n).file_size for n in names)
                            print(f"     包含 {len(names)} 个文件，共 {total_size:,} bytes", flush=True)
                            for n in names[:8]:
                                print(f"       • {n}", flush=True)
                            if len(names) > 8:
                                print(f"       ... 及其他 {len(names) - 8} 个文件", flush=True)
                    except Exception:
                        pass
                self._audit("allow", command, f"risk={risk.value}, phrase_confirmed")
                return True
            print(f"\n  ⛔ 确认短语不匹配，操作已取消。", flush=True)
            self._audit("reject", command, f"risk={risk.value}, phrase_mismatch")
            return False
        try:
            ans = input(f"  确认执行？(y/N) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._audit("reject", command, "input_interrupted")
            return False
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

    def _resolve_targets(self, command: str) -> list[str]:
        """从删除命令中解析目标文件/目录路径列表。"""
        targets = []
        parts = command.split()
        skip_next = False
        for i, p in enumerate(parts):
            if skip_next:
                skip_next = False
                continue
            if p.startswith("-"):
                if p in ("-rf", "-r", "-f") or p.startswith("-rf") or p.startswith("-r "):
                    continue
                # 跳过带参数的长选项
                if p in ("--recursive", "--force"):
                    continue
                skip_next = True
                continue
            if p in ("rm", "rmdir", "del", "rd", "Remove-Item", "find"):
                continue
            if p.startswith('"') or p.startswith("'"):
                continue
            targets.append(p)
        return targets

    def _shadow_backup(self, command: str) -> str | None:
        """删除前创建 zip 快照到 .chisel/trash/。

        扫描命令中引用的文件/目录，在删除执行前打包备份。
        返回备份文件路径，失败返回 None。
        """
        import zipfile

        targets = self._resolve_targets(command)
        if not targets:
            return None

        trash_dir = Path(self.workspace) / ".chisel" / "trash"
        trash_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = trash_dir / f"backup_{timestamp}.zip"

        files_added = 0
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for target in targets:
                    full = Path(self.workspace) / target
                    if not full.exists():
                        continue
                    if full.is_dir():
                        for f in full.rglob("*"):
                            if f.is_file():
                                arcname = str(f.relative_to(self.workspace))
                                zf.write(f, arcname)
                                files_added += 1
                    elif full.is_file():
                        zf.write(full, target)
                        files_added += 1
            if files_added == 0:
                zip_path.unlink(missing_ok=True)
                return None
            return str(zip_path)
        except Exception as e:
            # 备份失败不阻断删除流程
            if zip_path.exists():
                zip_path.unlink(missing_ok=True)
            print(f"  ⚠️ Shadow backup failed: {e}", flush=True)
            return None

    def persist_audit(self, workspace: str) -> None:
        if not self._audit_log:
            return
        path = Path(workspace) / ".chisel" / "audit.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for entry in self._audit_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._audit_log = []