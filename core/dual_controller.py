"""OpenHands 风格的双 AI 控制器：PlannerAgent 规划 + CodeActAgent 执行。

架构：
  1. PlannerAgent 探索项目，输出结构化计划（含任务依赖）
  2. 计划注入 CodeActAgent 的上下文
  3. CodeActAgent 执行计划，逐任务推进
  4. 遇到阻塞时调 replan_request → PlannerAgent 重新规划
  5. 重复直到所有任务完成

设计来源（OpenHands PlannerAgent / CodeActAgent / event stream 架构，自写实现）：
  - OpenHands 用事件流（PlanAction）在 agent 间通信
  - 本项目简化为：计划写入 PlanTracker，通过 replan_request 工具触发重规划
"""
from __future__ import annotations

import json
import sys
import time

from core.planner_agent import run_planner
from core.runtime import ExecutionContext
from tools import all_tools, execute_tool, register_tool

REPLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "replan_request",
        "description": "Request the planner to revise the current plan. "
                       "Call this when you encounter a blocking issue that makes "
                       "the current plan impossible to follow. "
                       "Explain what went wrong and what needs to change.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why replanning is needed and what should change"},
            },
            "required": ["reason"],
        },
    },
}


def _handle_replan(ctx, args: dict) -> str:
    ctx._replan_requested = True
    ctx._replan_reason = args.get("reason", "Unknown reason")
    return "Replan requested. The planner will revise the plan."


register_tool(REPLAN_SCHEMA, _handle_replan)

# CodeActAgent 的 system prompt
CODEXEC_SYSTEM_PROMPT = """You are a coding agent executing a predefined plan. You have full read/write/execute access.

{env}

{repo_map}

The current plan is shown below. Follow it step by step:
- Complete tasks in dependency order
- Update task status with plan(update) as you progress
- Mark tasks as verified after testing
- If you encounter a blocking issue, call replan_request with details

Tools available:
- bash: execute commands (in Docker sandbox)
- read_file / write_file / edit_file: file operations
- plan: update task progress
- replan_request: request plan revision
- git / terminal / code_navigate / web_fetch / web_search / rag_search

IMPORTANT: Always include tool calls until the task is complete.
"""


class DualController:
    """协调 PlannerAgent 和 CodeActAgent 的双 AI 控制器。"""

    def __init__(self, workspace: str, client, ctx: ExecutionContext):
        self.workspace = workspace
        self.client = client
        self.ctx = ctx
        self.ctx.plan.mode = "openhands"

    def run(self, task: str, max_steps: int = 50) -> str:
        """主入口：规划 → 执行 → 可能重规划 → 返回结果。"""
        # Phase 1: PlannerAgent 规划
        print("\n" + "=" * 60, flush=True)
        print("  Phase 1: Planning (PlannerAgent)", flush=True)
        print("=" * 60, flush=True)

        plan_result = run_planner(task, self.ctx)
        print(f"  Planner result: {plan_result[:200]}", flush=True)

        if not self.ctx.plan.tasks:
            return "Failed to create a plan. Try using Cline mode instead."

        # Phase 2: CodeActAgent 执行（可能多次重规划）
        for iteration in range(3):  # 最多 3 次重规划
            print(f"\n{'='*60}\n  Phase 2: Execution (CodeActAgent) - Iteration {iteration + 1}\n{'='*60}", flush=True)

            self.ctx._replan_requested = False
            result = self._run_executor(task, max_steps)

            if not self.ctx._replan_requested:
                return result

            # 重规划
            print(f"\n  Replan requested: {self.ctx._replan_reason[:200]}", flush=True)
            if iteration < 2:
                replan_task = (
                    f"The executor encountered a blocking issue while executing the plan.\n"
                    f"Original task: {task}\n"
                    f"Current plan: {self.ctx.plan.to_text()}\n"
                    f"Blocking issue: {self.ctx._replan_reason}\n\n"
                    f"Please revise the plan to address this issue."
                )
                plan_result = run_planner(replan_task, self.ctx)
                print(f"  Replan result: {plan_result[:200]}", flush=True)

        return "Max replan iterations reached. Task may be incomplete."

    def _run_executor(self, task: str, max_steps: int) -> str:
        """CodeActAgent 执行循环。"""
        # 构建环境信息
        env = f"Environment: {sys.platform} | Python {sys.version.split()[0]} | Working directory: {self.workspace}"

        from perception.repo_map import get_repo_map
        repo_map = get_repo_map(self.workspace)

        messages = [
            {"role": "system", "content": CODEXEC_SYSTEM_PROMPT.format(env=env, repo_map=repo_map)},
            {"role": "system", "content": ""},  # 计划占位
            {"role": "user", "content": task},
        ]
        self.ctx.plan.inject(messages)

        for step in range(1, max_steps + 1):
            # 注入计划与警告
            self.ctx.plan.inject(messages)
            for w in self.ctx.loop.drain_warnings() + self.ctx.mistake.drain_warnings():
                messages.append({"role": "user", "content": w})

            print(f"\n[CodeAct {step}] Requesting model...", flush=True)
            try:
                resp = self.client.chat(messages, self._executor_tools())
            except Exception as e:
                print(f"  LLM error: {e}", flush=True)
                time.sleep(1)
                continue

            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                print(f"\n{'='*60}\n✅ CodeActAgent completed ({step} steps)\n{'='*60}\n{final}\n", flush=True)
                return final

            messages.append(self._assistant_msg(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    result = "Invalid JSON arguments."
                else:
                    print(f"\n  [{step}] {name}: {json.dumps(args, ensure_ascii=False)[:200]}", flush=True)
                    result = execute_tool(name, args, self.workspace, self.ctx)
                    print(f"    ↳ {str(result)[:300]}", flush=True)

                if name not in ("plan", "replan_request"):
                    self.ctx.loop.note_call(name, args)
                self.ctx.mistake.track(result)

                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": self.ctx.context.truncate_output(str(result))})

                # 检测 replan_request
                if name == "replan_request":
                    return result

            # 上下文压缩
            self.ctx.context.compress_context(messages, self.client, 60000, pinned=self.ctx.pinned())

            if self.ctx.loop.should_abort() or self.ctx.mistake.should_abort():
                print("\n⚠️ Loop guard triggered, stopping.", flush=True)
                return "Task stopped due to suspected infinite loop."

        print("\n⚠️ Max steps reached.", flush=True)
        return ""

    def _executor_tools(self):
        """CodeActAgent 的全部工具（含 replan_request）。"""
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