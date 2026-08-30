"""死循环检测与连续错误追踪。

设计来源（借鉴 Cline loop-detection.ts / mistake-tracker.ts 的思路，自写）：
  - 循环键 = (工具名, 参数排序序列化)，只统计**连续**相同签名；中间穿插任何其他
    工具调用即重置计数 —— 避免把正常调试循环（跑测试 → 改 → 跑测试）误判为死循环。
  - soft 阈值向模型注入"请换策略"警告；hard 阈值判定死循环、让主循环中止。
"""

from __future__ import annotations

import json
import re

# 连续失败判定的错误特征（宽松匹配，靠"连续计数"避免单次误判）
_ERROR_RE = re.compile(
    r"出错|失败|错误|Error|Traceback|Exception|mismatch|未精确匹配|"
    r"FileNotFoundError|AssertionError|FAILED|failed",
    re.IGNORECASE,
)

# 元工具不参与循环计数（plan 更新 / ask_user 重复调用是正常行为）
_META_TOOLS = {"plan", "ask_user", "attempt_completion"}


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
                f"检测到连续 {self._count} 次重复调用 {name}（参数完全相同），结果没有推进。"
                f"请停止盲目重试：先用 read_file / rag_search 确认当前真实状态，"
                f"或换一种思路，不要重复相同操作。"
            )
        if self._count == self.hard:
            self._warnings.append(
                f"已连续 {self._count} 次重复调用 {name}，判定疑似死循环。"
                f"系统将停止当前任务（已做的修改保留在工作区）。"
            )

    def should_abort(self) -> bool:
        return self._count >= self.hard

    def drain_warnings(self) -> list[str]:
        w, self._warnings = self._warnings, []
        return w


class MistakeTracker:
    """连续错误追踪（Cline 风格：分 3 种类型）。

    错误类型：
      - api_error：API 连接/限流错误
      - invalid_tool_call：工具参数错误
      - tool_execution_failed：工具执行失败
    """

    def __init__(self, soft: int = 3, hard: int = 5):
        self.soft = soft
        self.hard = hard
        self._errors: dict[str, int] = {
            "api_error": 0,
            "invalid_tool_call": 0,
            "tool_execution_failed": 0,
        }
        self._warnings: list[str] = []

    def _classify(self, result: str) -> str | None:
        """对错误结果分类。返回 None 表示无错误。"""
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

    def track(self, result: str) -> None:
        cls = self._classify(result)
        if cls:
            self._errors[cls] += 1
            # 任意类型达到 hard 阈值就触发
            total = sum(self._errors.values())
            if total == self.soft:
                details = "; ".join(f"{k}={v}" for k, v in self._errors.items() if v > 0)
                self._warnings.append(
                    f"Detected {total} consecutive errors ({details}). "
                    f"Stop and analyze the root cause: use read_file to check "
                    f"actual file content, verify assumptions, and try a different approach."
                )
            if total >= self.hard:
                self._warnings.append(
                    f"Detected {total} consecutive errors, cannot continue. "
                    f"Task will be stopped (changes preserved in workspace)."
                )
        else:
            for k in self._errors:
                self._errors[k] = 0

    def should_abort(self) -> bool:
        return sum(self._errors.values()) >= self.hard

    def drain_warnings(self) -> list[str]:
        w, self._warnings = self._warnings, []
        return w
