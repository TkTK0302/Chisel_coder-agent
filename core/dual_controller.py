"""Dual-agent controller: orchestrates Planning Agent and sub-agents (OpenHands-style).

Architecture (matching OpenHands delegate pattern):
  1. Planning Agent explores the project, creates a plan, delegates tasks
  2. Each delegated task runs as a sub-agent (independent LLM conversation)
  3. Sub-agents have full read/write/execute access
  4. Planning Agent collects results and returns a summary

Design reference: OpenHands DelegateExecutor + LocalConversation + PlanningSection

Q5/Q10 improvements:
  - Planner timeout auto-falls back to single-agent mode
  - Up to 3 retries for failed task plans
  - Post-execution pytest regression check
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from core.delegate_tool import cleanup_sub_agents
from core.planning_agent import run_planner
from core.runtime import ExecutionContext
from tools import all_tools, execute_tool


class DualController:
    """Orchestrates the Planning Agent and sub-agent execution."""

    def __init__(self, workspace: str, client, ctx: ExecutionContext):
        self.workspace = workspace
        self.client = client
        self.ctx = ctx
        self.ctx.plan.mode = "multi"

    def run(self, task: str, max_steps: int = 50) -> str:
        """Run the full planning + execution pipeline with retry and fallback."""
        print(f"\n  🧠 任务分析中...", flush=True)

        try:
            for retry in range(3):
                # Phase 1: Planning Agent creates plan and delegates tasks
                result = run_planner(task, self.ctx)

                # Q5: Planner 返回 None → 自动回退到 single 模式
                if result is None:
                    print("  🔄 回退到单 Agent 模式...", flush=True)
                    return self._fallback_to_single(task, max_steps)

                if not result:
                    return "Planning phase completed without producing a result."

                # Q10: 检查计划执行状态
                failed_tasks = [t for t in self.ctx.plan.tasks
                              if t.status not in ("done", "verified")]
                if not failed_tasks:
                    # 全部成功 → 运行回归测试
                    regression_result = self._run_regression_tests()
                    if regression_result:
                        result += f"\n\n{regression_result}"
                    return result

                # 有失败任务 → 重新规划（最多 3 次）
                if retry < 2:
                    print(f"  ⚠️ {len(failed_tasks)} 个任务未完成，重新规划...", flush=True)
                    for t in failed_tasks:
                        t.status = "pending"
                else:
                    # 最终失败：生成报告
                    return self._generate_failure_report(result)

            return self._generate_failure_report("")

        finally:
            cleanup_sub_agents()

    def _fallback_to_single(self, task: str, max_steps: int) -> str:
        """Q5: Planner 超时后回退到单 Agent 模式执行。"""
        from agent import Agent, build_system_prompt
        from llm import LLMClient

        self.ctx.plan.mode = "single"
        agent = Agent(
            self.workspace, self.client, max_steps=max_steps,
            plan_mode="single", window_size=3,
        )
        return agent.run(task)

    def _run_regression_tests(self) -> str:
        """Q10: 所有子 Agent 完成后运行回归测试。"""
        ws = Path(self.workspace)
        test_dirs = [ws / "tests", ws / "test"]
        existing = [d for d in test_dirs if d.exists()]
        if not existing:
            return ""

        print("  🧪 回归测试中...", flush=True)
        try:
            result = subprocess.run(
                ["pytest", str(existing[0]), "-x", "--tb=short"],
                capture_output=True, text=True, timeout=60,
                cwd=str(ws),
            )
            if result.returncode != 0:
                return f"⚠️ 回归测试失败：\n{result.stdout[-2000:]}"
            return "✅ 回归测试通过"
        except subprocess.TimeoutExpired:
            return "⚠️ 回归测试超时（60s）"
        except FileNotFoundError:
            return ""  # pytest 未安装

    def _generate_failure_report(self, partial_result: str) -> str:
        """Q10: 生成包含已完成和未完成任务的失败报告。"""
        done = [t for t in self.ctx.plan.tasks if t.status in ("done", "verified")]
        failed = [t for t in self.ctx.plan.tasks if t.status not in ("done", "verified")]
        lines = ["⚠️ 部分任务未完成："]
        if done:
            lines.append(f"\n已完成 ({len(done)}个)：")
            for t in done:
                lines.append(f"  ✓ {t.id}: {t.description}")
        if failed:
            lines.append(f"\n未完成 ({len(failed)}个)：")
            for t in failed:
                lines.append(f"  ✗ {t.id}: {t.description} [{t.status}]")
        if partial_result:
            lines.append(f"\n最后执行结果：\n{partial_result[-1000:]}")
        return "\n".join(lines)

    def _executor_tools(self):
        return all_tools()

    @staticmethod
    def _assistant_msg(msg) -> dict:
        return {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        }