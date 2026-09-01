"""Planning Agent with delegate tool (OpenHands-style).

The Planning Agent:
  1. Explores the project (read-only)
  2. Creates a structured plan with task dependencies
  3. Delegates each task to sub-agents via the delegate tool
  4. Collects results and returns a summary

Design reference: OpenHands PlanningSection prompt + feasibility check + subtask verification
"""
from __future__ import annotations

import json
import sys
import time

from core.runtime import ExecutionContext
from tools import all_tools, execute_tool

PLANNER_PROMPT = """You are a Planning Agent that analyzes codebases and creates structured plans.

<ROLE>
* Your primary role is to create a detailed step-by-step plan. You are NOT an executor - you only plan and delegate.
* Once you have enough information, create the plan using `plan(action="create")` and delegate tasks using `delegate(task="...")`.
* IMPORTANT: After at most 10 exploration steps, you MUST create a plan and delegate tasks. Do not explore indefinitely.
</ROLE>

<IMPORTANT_PRINCIPLES>
* **Don't make large assumptions about user intent.** The goal is to present a well-researched plan.
* **Be efficient.** Read the file once, understand it, then create the plan. Do not re-read the same file multiple times.
* **Plan first, then delegate.** Create the plan with `plan(action="create")`, then delegate each task with `delegate()`.
</IMPORTANT_PRINCIPLES>

<EFFICIENCY>
* Each action is expensive. Read the file once, then plan. Do not grep the same file multiple times.
* Use `read_file` to read the full file in one call, then `code_navigate` for specific symbols.
</EFFICIENCY>

<PLANNING_WORKFLOW>
## Phase 1: Explore (max 8 steps)
Read the file, understand its structure, identify relevant classes and functions.

## Phase 2: Plan (must complete within 3 steps)
Create the plan with `plan(action="create", tasks=[...])`. Each task must have id, description, depends_on.

## Phase 3: Delegate (execute plan)
For each task in dependency order, call `delegate(task="...")` to execute it.
After each delegation, update the plan with `plan(action="update")`.
</PLANNING_WORKFLOW>

You have these tools available:
- read_file, code_navigate, rag_search: explore the codebase
- bash: read-only shell commands (ls, cat, grep, find, head, tail)
- plan: create and update the plan
- delegate: execute a task via a sub-agent
- ask_user: ask the user for clarification
"""


def run_planner(task: str, ctx: ExecutionContext) -> str:
    """Run the Planning Agent to create a plan and delegate tasks."""
    from agent import build_system_prompt

    planner_tools = _filter_readonly_tools()

    messages = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"Analyze this task and create a plan with delegated subtasks:\n\n{task}"},
    ]

    for step in range(1, 25):
        # 强制在第 12 步时要求创建计划
        if step == 12 and not ctx.plan.tasks:
            messages.append({
                "role": "user",
                "content": "You have explored enough. You MUST now create a plan using plan(action='create') and delegate tasks using delegate(). Do not explore further."
            })

        print(f"  [Planner] Step {step}...", flush=True)
        try:
            resp = ctx.client.chat(messages, planner_tools)
        except Exception as e:
            print(f"  [Planner] LLM error: {e}", flush=True)
            time.sleep(1)
            continue

        msg = resp.choices[0].message
        if not getattr(msg, "tool_calls", None):
            content = msg.content or ""
            if content:
                return content
            return "Planner completed without creating a plan."

        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [
                             {"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                             for tc in msg.tool_calls]})

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                result = "Invalid JSON arguments."
            else:
                if name == "bash":
                    cmd = args.get("command", "")
                    if _is_readonly(cmd):
                        result = ctx.ensure_sandbox().run(cmd, ctx.workspace)
                    else:
                        result = "Blocked: only read-only commands allowed in planner."
                elif name == "delegate":
                    print(f"  [Planner] Delegating task: {args.get('task', '')[:100]}...", flush=True)
                    result = execute_tool(name, args, ctx.workspace, ctx)
                    # 改进：子任务验证 —— delegate 后自动检查语法
                    if "Task completed" in result or "Sub-agent result" in result:
                        verify_result = _verify_delegated_task(result, ctx)
                        if verify_result:
                            result += f"\n{verify_result}"
                    print(f"  [Planner] Sub-agent result: {result[:200]}...", flush=True)
                else:
                    result = execute_tool(name, args, ctx.workspace, ctx)
                print(f"    [{step}] {name}: {str(result)[:200]}", flush=True)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)[:3000]})

    return "Planner reached max steps."


def _verify_delegated_task(result: str, ctx: ExecutionContext) -> str:
    """改进：子任务验证 —— 检查编辑后的文件语法。"""
    import ast
    from pathlib import Path

    lines = result.splitlines()
    for line in lines:
        if line.startswith("Edited ") or line.startswith("Written "):
            parts = line.split()
            if len(parts) >= 2:
                path = parts[1].rstrip(":")
                if path.endswith(".py"):
                    full_path = Path(ctx.workspace) / path
                    if full_path.exists():
                        try:
                            ast.parse(full_path.read_text(encoding="utf-8"))
                            return f"  [Verify] {path}: syntax OK"
                        except SyntaxError as e:
                            return f"  [Verify] {path}: Syntax error at line {e.lineno}: {e.msg}"
    return ""


def _filter_readonly_tools():
    readonly_names = {"read_file", "code_navigate", "rag_search", "plan", "delegate", "ask_user", "attempt_completion",
                      "web_fetch", "web_search"}
    tools = []
    for t in all_tools():
        name = t["function"]["name"]
        if name == "bash":
            tools.append({
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Execute read-only shell commands: ls, cat, grep, find, head, tail, pwd, which, echo.",
                    "parameters": t["function"]["parameters"],
                },
            })
        elif name in readonly_names:
            tools.append(t)
    return tools


def _is_readonly(cmd: str) -> bool:
    readonly = {"ls", "cat", "grep", "find", "head", "tail", "wc", "echo", "pwd",
                "which", "file", "sort", "uniq", "cut", "diff", "stat", "du",
                "type", "printenv", "env"}
    first = cmd.strip().split("|")[0].strip().split()[0] if cmd.strip() else ""
    return first in readonly or cmd.startswith("python -c ") or "python --version" in cmd