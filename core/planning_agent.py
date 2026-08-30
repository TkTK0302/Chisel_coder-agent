"""OpenHands-style Planning Agent with delegate tool.

The Planning Agent:
  1. Explores the project (read-only)
  2. Creates a structured plan with task dependencies
  3. Delegates each task to sub-agents via the delegate tool
  4. Collects results and returns a summary

Design reference: OpenHands PlanningSection prompt + DelegateExecutor
"""
from __future__ import annotations

import json
import sys
import time

from core.runtime import ExecutionContext
from tools import all_tools, execute_tool

PLANNER_PROMPT = """You are a Planning Agent that analyzes codebases and creates implementation plans.

<ROLE>
Your primary role is to assist users by creating a comprehensive step-by-step implementation plan.
You should be thorough, methodical, and prioritize quality over speed.
</ROLE>

<IMPORTANT_PRINCIPLES>
* Don't make large assumptions about user intent. The goal is to present a well-researched plan.
* Ask clarifying questions when needed via the ask_user tool.
* Prioritize technical accuracy over validating the user's beliefs.
</IMPORTANT_PRINCIPLES>

<EFFICIENCY>
* Each action you take is somewhat expensive. Wherever possible, combine multiple actions.
* When exploring the codebase, use efficient tools like grep and code_navigate.
</EFFICIENCY>

<PLANNING_WORKFLOW>
## Phase 1: Initial Understanding
Explore the codebase to understand the project structure and relevant files.
Use read_file, code_navigate, rag_search, and bash (read-only commands) to gather context.

## Phase 2: Planning
Create a detailed plan using the plan tool with action=create.
Each task should have:
- id: unique identifier
- description: what needs to be done
- depends_on: list of task IDs that must be completed first (use empty list if none)

## Phase 3: Execution
Delegate each task to a sub-agent using the delegate tool.
Delegate tasks in dependency order (a task's dependencies must be completed first).
After each delegation, update the task status with plan(update).
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

    # Read-only tools for the Planner
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
                    # Delegate tool is handled by the registered handler
                    result = execute_tool(name, args, ctx.workspace, ctx)
                    print(f"  [Planner] Sub-agent result: {result[:200]}...", flush=True)
                else:
                    result = execute_tool(name, args, ctx.workspace, ctx)
                print(f"    [{step}] {name}: {str(result)[:200]}", flush=True)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)[:3000]})

    return "Planner reached max steps."


def _filter_readonly_tools():
    """Return tools suitable for the read-only planner phase."""
    readonly_names = {"read_file", "code_navigate", "rag_search", "plan", "delegate", "ask_user", "attempt_completion",
                      "web_fetch", "web_search"}
    tools = []
    for t in all_tools():
        name = t["function"]["name"]
        if name == "bash":
            # Replace with read-only bash
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