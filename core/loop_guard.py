"""死循环检测 + 连续错误追踪 + 错误恢复指引 + 错误持久化 + 独立重试策略。

改进：
  - Q10: _current_tool 脏值修复（tool_name 改为必传）
  - Q9: delegate/escalate 加入元工具豁免
  - Q7: 警告措辞优化为中性、结构化、可操作
  - Q3: 错误分类改用 exit_code 优先判断
  - Q4: 三类错误分开计数，api_error 不触发终止
  - Q6: 成功加权清零（减半而非全量归零）
  - Q12: 警告累积递进（不清空，包含递增强度）
  - Q8: 安全点追踪（记录错误开始时的 Git HEAD）
"""
from __future__ import annotations

import json
from pathlib import Path

# Q9: delegate/escalate 加入元工具豁免
_META_TOOLS = {"plan", "ask_user", "attempt_completion", "remember",
               "memory_search", "delegate", "escalate"}

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
        self._warning_level = 0  # Q12: 追踪警告强度，不清空

    def note_call(self, name: str, args: dict) -> None:
        if name in _META_TOOLS:
            return
        key = (name, json.dumps(args, sort_keys=True))
        if key == self._prev_key:
            self._count += 1
        else:
            self._prev_key = key
            self._count = 1
            self._warning_level = 0  # 换了调用方式，重置警告强度

        # Q7: 中性、结构化、可操作的警告措辞
        if self._count == self.soft:
            self._warning_level = 1
            self._warnings.append(
                f"[系统提示] 检测到连续 {self._count} 次用相同参数调用 {name}。"
                f"当前方法可能不奏效。建议："
                f"1. 使用 read_file 查看文件当前内容（内容可能已变化）"
                f"2. 确认参数与文件内容是否匹配"
                f"3. 如果匹配失败，用新的内容重新尝试"
            )
        elif self._count > self.soft and self._count < self.hard:
            self._warning_level = 2
            self._warnings.append(
                f"[系统提示] 仍在重复调用 {name}（第 {self._count} 次）。"
                f"强烈建议立即使用 read_file 确认文件状态，不要继续重试。"
            )
        if self._count == self.hard:
            self._warning_level = 3
            self._warnings.append(
                f"[系统提示] 连续 {self._count} 次相同调用，已达到上限。"
                f"任务将被终止，工作目录中的修改会保留。"
            )

    def should_abort(self) -> bool:
        return self._count >= self.hard

    def drain_warnings(self) -> list[str]:
        """Q12: 返回当前所有警告，不再清空。警告强度随 _warning_level 递进。"""
        return list(self._warnings)


class MistakeTracker:
    """连续错误追踪：3 种类型分开计数 + 错误恢复指引 + 持久化 + 独立重试。"""

    def __init__(self, soft: int = 3, hard: int = 5):
        self.soft = soft
        self.hard = hard
        # Q4: 三类错误分开计数，api_error 不触发 abort
        self._errors: dict[str, int] = {
            "api_error": 0,
            "invalid_tool_call": 0,
            "tool_execution_failed": 0,
        }
        self._warnings: list[str] = []
        self._warning_level = 0  # Q12: 追踪警告强度
        self._current_tool: str = ""
        self._error_log: list[dict] = []  # 持久化用
        self._consecutive_successes: int = 0  # Q6: 连续成功计数
        # Q8: 安全点追踪
        self.safe_point_sha: str | None = None
        self._error_started: bool = False

    # ------------------------------------------------------------------
    # Q3: 错误分类 —— exit_code 优先，关键词匹配兜底
    # ------------------------------------------------------------------

    def _classify(self, result: str, exit_code: int | None = None) -> str | None:
        """分类错误类型。exit_code 优先判断，关键词匹配兜底。"""
        if not isinstance(result, str):
            return None

        # Q3: exit_code == 0 且无异常堆栈 → 不是错误
        if exit_code == 0:
            has_traceback = "Traceback (most recent call last)" in result
            has_assertion = "AssertionError" in result
            if not has_traceback and not has_assertion:
                return None

        # api_error 关键词（网络/服务端问题）
        if any(kw in result for kw in ["APIConnectionError", "APIError",
                                        "RateLimit", "Timeout", "ConnectionError"]):
            return "api_error"

        # invalid_tool_call 关键词（模型输出格式问题）
        if any(kw in result for kw in ["Invalid JSON", "unknown tool",
                                        "missing required"]):
            return "invalid_tool_call"

        # tool_execution_failed 关键词（操作本身失败）
        # Q3: "Error:" 只在 exit_code != 0 或伴随 Traceback 时才判定为错误
        if any(kw in result for kw in ["Tool execution error", "Access denied",
                                        "edit_file failed", "Traceback", "Exception",
                                        "FAILED", "AssertionError"]):
            return "tool_execution_failed"
        if exit_code is not None and exit_code != 0 and "Error:" in result:
            return "tool_execution_failed"

        return None

    # ------------------------------------------------------------------
    # Q10: track —— tool_name 必传，强制更新 _current_tool
    # ------------------------------------------------------------------

    def track(self, result: str, tool_name: str,
              exit_code: int | None = None) -> None:
        """追踪一次工具执行结果。tool_name 必传，exit_code 可选。"""
        self._current_tool = tool_name  # Q10: 强制更新，不留脏值
        cls = self._classify(result, exit_code)

        if cls:
            self._consecutive_successes = 0  # Q6: 错误中断连续成功
            # Q8: 记录错误开始时的安全点（仅在首次错误时）
            if not self._error_started:
                self._error_started = True
                # safe_point_sha 由外部设置（agent.py 在 track 前调用 set_safe_point）

            self._errors[cls] += 1
            self._error_log.append({
                "tool": self._current_tool,
                "error_type": cls,
                "result": result[:200],
            })

            # Q4: 只有 tool_execution_failed 参与 soft/hard 判断
            tf_count = self._errors["tool_execution_failed"]

            # 软警告：tool_execution_failed 达到 soft 阈值
            if tf_count == self.soft:
                self._warning_level = 1
                self._warnings.append(
                    f"[系统提示] 连续 {tf_count} 次操作失败（{self._current_tool}）。"
                    f"建议暂停并分析根因：使用 read_file 查看文件当前内容，"
                    f"确认你的假设是否仍然正确，或尝试完全不同的方式。"
                )

            # 工具级限制警告
            tool_limit = _TOOL_RETRY_LIMITS.get(self._current_tool, self.hard)
            if tf_count >= tool_limit and self._warning_level < 2:
                self._warning_level = 2
                self._warnings.append(
                    f"[系统提示] 工具 {self._current_tool} 已连续失败 {tf_count} 次"
                    f"（上限 {tool_limit} 次）。"
                    f"这个工具当前不适合继续使用，请换一种方式："
                    f"如果是 edit_file 失败，先 read_file 确认内容；"
                    f"如果是 bash 失败，检查命令是否拼写正确。"
                )

            # 硬终止警告
            if tf_count >= self.hard:
                self._warning_level = 3
                self._warnings.append(
                    f"[系统提示] 操作连续失败 {tf_count} 次，已达到上限。"
                    f"任务将被终止，工作目录中的修改会保留。"
                )

            # api_error 单独警告（不参与终止判断）
            api_count = self._errors["api_error"]
            if api_count >= 3 and api_count % 3 == 0:
                self._warnings.append(
                    f"[系统提示] API 通信连续出现问题 {api_count} 次。"
                    f"可能是网络波动或服务端限流，系统会自动重试。"
                )

            # invalid_tool_call 单独警告
            iv_count = self._errors["invalid_tool_call"]
            if iv_count >= 3:
                self._warnings.append(
                    f"[系统提示] 工具调用格式连续 {iv_count} 次无效。"
                    f"可能是上下文过长导致输出格式异常，建议压缩上下文。"
                )

        else:
            # Q6: 成功加权清零 —— 一次成功减半，两次成功归零
            self._consecutive_successes += 1
            if self._consecutive_successes == 1:
                for k in self._errors:
                    self._errors[k] //= 2
            elif self._consecutive_successes >= 2:
                for k in self._errors:
                    self._errors[k] = 0
                self._error_log = []
                self._error_started = False
                self._warning_level = 0

    # ------------------------------------------------------------------
    # Q4: should_abort —— 只检查 tool_execution_failed
    # ------------------------------------------------------------------

    def should_abort(self) -> bool:
        """Q4: 只有 tool_execution_failed 达到 hard 阈值才触发终止。
        api_error 和 invalid_tool_call 不触发终止，只触发警告。
        """
        return self._errors["tool_execution_failed"] >= self.hard

    # ------------------------------------------------------------------
    # Q8: 安全点管理
    # ------------------------------------------------------------------

    def set_safe_point(self, sha: str) -> None:
        """Q8: 记录错误开始累积时的 Git HEAD，作为回滚目标。"""
        if self._error_started and self.safe_point_sha is None:
            self.safe_point_sha = sha

    def clear_safe_point(self) -> None:
        """成功清零时同时清除安全点。"""
        self.safe_point_sha = None

    # ------------------------------------------------------------------
    # Q12: drain_warnings —— 累积不清空
    # ------------------------------------------------------------------

    def drain_warnings(self) -> list[str]:
        """Q12: 返回当前所有警告，不再清空。警告强度随 _warning_level 递进。"""
        return list(self._warnings)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

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