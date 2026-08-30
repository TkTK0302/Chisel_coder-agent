"""死循环检测 + 连续错误追踪 + 错误恢复指引 + 错误持久化 + 独立重试策略。

改进：
  - 错误恢复指引（Cline 风格）：告诉 AI 具体该怎么做
  - 错误记录持久化：保存到 .chisel/errors.json
  - 独立重试策略：每个工具可配置不同的重试上限
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_META_TOOLS = {"plan", "ask_user", "attempt_completion", "remember", "memory_search"}

# 独立重试策略：每个工具的最大连续失败次数
_TOOL_RETRY_LIMITS = {
    "bash": 4,
    "edit_file": 5,
    "write_file": 3,
    "read_file": 3,
    "git": 3,
    "rag_search": 3,
    "code_navigate": 3,
    "web_fetch": 3,
    "web_search": 3,
    "terminal": 3,
    "delegate": 3,
}


class LoopGuard:
    """连续相同工具调用检测。"""

    def __init__(self, soft: int = 3, hard: int = 5):
        self.soft = soft
        self.hard = hard
        self._prev_key: tuple | None = None
        self._count = 0
        self._warnings: list[str] = []

    def note_call(self, name: str, args: dict) -> None:
        if name in _META_TOOLS:
            return
        key = (name, json.dumps(args, sort_keys=True))
        if key == self._prev_key:
            self._count += 1
        else:
            self._prev_key = key
            self._count = 1
        if self._count == self.soft:
            self._warnings.append(
                f"Detected {self._count} consecutive identical calls to {name} with no progress. "
                f"Stop retrying blindly: use read_file to check the actual file state, "
                f"or try a completely different approach. For example, if edit_file keeps failing, "
                f"first read the file to see its current content."
            )
        if self._count == self.hard:
            self._warnings.append(
                f"Detected {self._count} consecutive identical calls to {name}. "
                f"System will stop the task (changes preserved in workspace)."
            )

    def should_abort(self) -> bool:
        return self._count >= self.hard

    def drain_warnings(self) -> list[str]:
        w, self._warnings = self._warnings, []
        return w


class MistakeTracker:
    """连续错误追踪（Cline 风格：3 种类型 + 错误恢复指引 + 持久化 + 独立重试）。"""

    def __init__(self, soft: int = 3, hard: int = 5):
        self.soft = soft
        self.hard = hard
        self._errors: dict[str, int] = {
            "api_error": 0,
            "invalid_tool_call": 0,
            "tool_execution_failed": 0,
        }
        self._warnings: list[str] = []
        self._current_tool: str = ""
        self._error_log: list[dict] = []  # 持久化用

    def _classify(self, result: str) -> str | None:
        if not isinstance(result, str):
            return None
        if any(kw in result for kw in ["APIConnectionError", "APIError", "RateLimit", "Timeout", "ConnectionError"]):
            return "api_error"
        if any(kw in result for kw in ["Invalid JSON", "unknown tool", "missing required", "not found"]):
            return "invalid_tool_call"
        if any(kw in result for kw in ["Tool execution error", "Access denied", "edit_file failed",
                                        "Error:", "Traceback", "Exception", "FAILED", "AssertionError"]):
            return "tool_execution_failed"
        return None

    def track(self, result: str, tool_name: str = "") -> None:
        self._current_tool = tool_name or self._current_tool
        cls = self._classify(result)
        if cls:
            self._errors[cls] += 1
            self._error_log.append({"tool": self._current_tool, "error_type": cls, "result": result[:200]})
            total = sum(self._errors.values())
            # soft 阈值警告
            if total == self.soft:
                self._warnings.append(
                    f"Detected {total} consecutive errors. "
                    f"Stop and analyze the root cause: use read_file to check actual file content, "
                    f"verify your assumptions, and try a completely different approach."
                )
            # 独立重试策略：检查当前工具是否超过上限
            tool_limit = _TOOL_RETRY_LIMITS.get(self._current_tool, self.hard)
            if total >= tool_limit:
                self._warnings.append(
                    f"Detected {total} consecutive errors on tool '{self._current_tool}' "
                    f"(limit: {tool_limit}). "
                    f"Stop and analyze the root cause: use read_file to check actual file content, "
                    f"verify your assumptions, and try a completely different approach. "
                    f"For example, if edit_file keeps failing, read the file first to see its current state."
                )
            if total >= self.hard:
                self._warnings.append(
                    f"Detected {total} consecutive errors, cannot continue. "
                    f"Task will be stopped (changes preserved in workspace)."
                )
        else:
            for k in self._errors:
                self._errors[k] = 0
            self._error_log = []

    def should_abort(self) -> bool:
        return sum(self._errors.values()) >= self.hard

    def drain_warnings(self) -> list[str]:
        w, self._warnings = self._warnings, []
        return w

    def persist(self, workspace: str) -> None:
        """持久化错误记录到 .chisel/errors.json。"""
        if not self._error_log:
            return
        path = Path(workspace) / ".chisel" / "errors.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.extend(self._error_log)
        existing = existing[-100:]  # 最多保留 100 条
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")