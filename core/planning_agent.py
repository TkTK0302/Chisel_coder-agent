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
* IMPORTANT: After at most 8 exploration steps, you MUST create a plan and delegate tasks. The system will force-stop you at step 10.
</ROLE>

<CRITICAL_RULE>
**FOR PYTHON FILE ANALYSIS: Your FIRST tool call MUST be `code_navigate(action="symbols", path="the_file.py")`.**
This returns ALL classes and functions with their signatures in a single call. It is 100x faster than grep.
**NEVER use bash grep to discover classes or functions.** code_navigate uses AST parsing — it is always correct and instantaneous.
**NEVER re-read the same file.** One read_file is enough. Use code_navigate for everything else.
</CRITICAL_RULE>

<IMPORTANT_PRINCIPLES>
* **Don't make large assumptions about user intent.** The goal is to present a well-researched plan.
* **Be efficient.** One `code_navigate(action="symbols")` call + one `read_file` call is all the exploration you need for a single-file task.
* **Plan first, then delegate.** Create the plan with `plan(action="create")`, then delegate each task with `delegate()`.
</IMPORTANT_PRINCIPLES>

<EFFICIENCY>
* `code_navigate(action="symbols", path="file.py")` → get ALL symbols in one shot. Use this FIRST.
* `code_navigate(action="definition", symbol="ClassName")` → get a specific class's methods.
* `bash` with grep is ONLY for searching non-Python files. For .py files, ALWAYS use code_navigate.
* `read_file` is for reading file content. Use it ONCE per file, not repeatedly.
</EFFICIENCY>

<PLANNING_WORKFLOW>
## Phase 1: Explore (max 5 steps, usually 2-3)
1. code_navigate(action="symbols", path="target.py") — get all classes/functions
2. read_file — read the file once to understand context
3. code_navigate(action="definition", symbol="KeyClass") — get details for specific classes
Then STOP exploring and move to Phase 2.

## Phase 2: Plan (1 step)
Create the plan with `plan(action="create", tasks=[...])`. Each task must have id, description, depends_on.

## Phase 3: Delegate (execute plan)
For each task in dependency order, call `delegate(task="...")` to execute it.
After each delegation, update the plan with `plan(action="update")`.
</PLANNING_WORKFLOW>

You have these tools available:
- code_navigate: PRIMARY tool for Python code exploration. Use action="symbols" to list all classes/functions.
- read_file: read file content (use ONCE per file)
- rag_search: semantic code search
- bash: read-only shell commands (ls, cat, find, head, tail, wc). Do NOT use grep for Python files — use code_navigate instead.
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
        # 第 8 步警告，第 10 步强制截断（不再给模型无限探索的机会）
        if step == 8 and not ctx.plan.tasks:
            messages.append({
                "role": "user",
                "content": (
                    "⚠️ You have reached the exploration limit. "
                    "Your NEXT response MUST call plan(action='create', tasks=[...]) — "
                    "do NOT call any other tool. Stop exploring. Create the plan NOW."
                ),
            })

        if step >= 10 and not ctx.plan.tasks:
            print("  [Planner] ⚠️ Max exploration steps without a plan. Terminating.", flush=True)
            return (
                "Planner terminated: exceeded 10 exploration steps without creating a plan. "
                "This usually means the task is better suited for single-agent mode. "
                "Tip: for single-file analysis, use --plan-mode single to skip the planner."
            )

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