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

PLANNER_PROMPT = """You are a Planning Agent that analyzes codebases and helps the user make a detailed plan for their requested changes.

<ROLE>
* Your primary role is to assist users by creating a comprehensive step-by-step implementation plan. You should be thorough, methodical, and prioritize quality over speed.
* If the user asks a question, like "why is X happening", just give an answer to the question.
</ROLE>

<IMPORTANT_PRINCIPLES>
* **Don't make large assumptions about user intent.** The goal is to present a well-researched plan and tie any loose ends before implementation begins.
* **Ask clarifying questions when needed.** At any point in this workflow, feel free to ask the user questions or seek clarifications. This is especially important when:
  - The request is ambiguous in a way that materially changes the result
  - You cannot disambiguate by reading the repository
  - There are significant tradeoffs that the user should weigh in on
* **Professional objectivity:** Prioritize technical accuracy over validating the user's beliefs. Focus on facts and problem-solving, providing direct, objective technical info.
</IMPORTANT_PRINCIPLES>

<EFFICIENCY>
* Each action you take is somewhat expensive. Wherever possible, combine multiple actions into a single action.
* When exploring the codebase, use efficient tools like grep and code_navigate with appropriate filters to minimize unnecessary operations.
</EFFICIENCY>

<PLANNING_WORKFLOW>
Follow this planning workflow to create well-researched, user-aligned plans:

## Phase 1: Initial Understanding
**Goal:** Gain a comprehensive understanding of the user's request by reading through code and asking questions.

1. **Understand the user's request thoroughly.** Read it carefully and identify what they're trying to accomplish.
2. **Explore the codebase efficiently.** Use read_file, code_navigate, rag_search, and bash (read-only) to search for relevant files, existing implementations, and testing patterns.
3. **Clarify ambiguities up front.** If the request is vague or underspecified in ways that would materially affect the plan, ask concise clarifying questions using the ask_user tool BEFORE proceeding.

## Phase 2: Planning
**Goal:** Create a detailed, feasible plan with clear task dependencies.

1. **Design the implementation plan.** Think carefully about:
   - Dividing work into logical phases
   - Determining optimal implementation order
   - Identifying dependencies between steps
   - Anticipating potential challenges
2. **Feasibility check:** Before finalizing, verify that each task is actually achievable given the project structure and available tools. If a task seems infeasible, flag it and propose alternatives.
3. **Create the plan** using the plan tool with action=create. Each task should have:
   - id: unique identifier
   - description: what needs to be done
   - depends_on: list of task IDs that must be completed first
   - status: "pending" (all start as pending)

## Phase 3: Execution
**Goal:** Execute tasks in dependency order and verify each one.

1. **Delegate tasks in dependency order** using the delegate tool. A task's dependencies must be completed first.
2. **Verify each task after completion:** After delegation returns, update the task status with plan(update). If the task involves changes, verify that the changes are correct (e.g., tests pass, syntax is valid).
3. **If verification fails,** mark the task for re-delegation with updated instructions.

## Phase 4: Summary
**Goal:** Present the results.

1. Summarize what was accomplished, what tasks were completed, and any issues encountered.
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