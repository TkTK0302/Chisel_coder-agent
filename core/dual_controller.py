"""Dual-agent controller: orchestrates Planning Agent and sub-agents (OpenHands-style).

Architecture (matching OpenHands delegate pattern):
  1. Planning Agent explores the project, creates a plan, delegates tasks
  2. Each delegated task runs as a sub-agent (independent LLM conversation)
  3. Sub-agents have full read/write/execute access
  4. Planning Agent collects results and returns a summary

Design reference: OpenHands DelegateExecutor + LocalConversation + PlanningSection
"""
from __future__ import annotations

import sys
import time

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
        """Run the full planning + execution pipeline."""
        print(f"\n{'='*60}", flush=True)
        print("  Multi-Agent Mode: Planning Agent + Sub-agents", flush=True)
        print(f"{'='*60}", flush=True)

        try:
            # Phase 1: Planning Agent creates plan and delegates tasks
            print("\n  Phase 1: Planning Agent", flush=True)
            print("  The planner explores the project, creates a plan,", flush=True)
            print("  and delegates tasks to sub-agents.", flush=True)
            print("-" * 40, flush=True)

            result = run_planner(task, self.ctx)

            # Phase 2: Collect results
            if not result:
                return "Planning phase completed without producing a result."

            return result

        finally:
            cleanup_sub_agents()

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