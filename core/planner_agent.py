"""OpenHands 风格的 PlannerAgent：只读探索 + 输出结构化计划。

在 OpenHands 模式中，PlannerAgent 负责：
  1. 分析项目结构和任务需求
  2. 输出带依赖关系的结构化计划
  3. 当 CodeActAgent 遇到阻塞时，重新规划

PlannerAgent 只有只读工具（read_file, bash ls/grep, code_navigate, rag_search）。
"""
from __future__ import annotations

import json
import sys
import time

from core.runtime import ExecutionContext
from tools import all_tools, execute_tool

PLANNER_SYSTEM_PROMPT = """You are a planning agent. Your job is to analyze the project and break down the user's task into a detailed, structured plan.

You have read-only access to the project. You can:
- Read files to understand the codebase
- List directories and search for files
- Navigate the codebase (find definitions, references)
- Search the codebase semantically

You CANNOT modify any files or execute commands that change the system.

{env}

Your output should be a plan created via the plan tool with action=create.
Each task should have:
- id: unique identifier
- description: what needs to be done
- status: "pending" (all tasks start as pending)
- depends_on: list of task IDs that must be completed first

Think step by step. First explore the project structure, then read relevant files,
then create a comprehensive plan with clear task dependencies.
"""


# PlannerAgent 可用工具：只读
_PLANNER_TOOL_NAMES = {"read_file", "code_navigate", "rag_search", "plan"}


def _planner_tools():
    return [t for t in all_tools() if t["function"]["name"] in _PLANNER_TOOL_NAMES]


def run_planner(task: str, ctx: ExecutionContext) -> str:
    """运行 PlannerAgent，返回计划创建结果（成功/失败信息）。"""
    # 构建只读的 bash 工具（允许 ls, cat, grep, find, head, tail, wc）
    readonly_bash = {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute read-only shell commands. Use for listing directories (ls), "
                           "viewing files (cat/head/tail), searching (grep/find), counting (wc). "
                           "Destructive commands are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Read-only shell command"},
                },
                "required": ["command"],
            },
        },
    }
    tools = [readonly_bash] + _planner_tools()

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT.format(env=_env_facts())},
        {"role": "user", "content": f"Analyze this task and create a plan:\n\n{task}"},
    ]

    max_steps = 20
    for step in range(1, max_steps + 1):
        try:
            resp = ctx.client.chat(messages, tools)
        except Exception as e:
            return f"PlannerAgent error: {e}"

        msg = resp.choices[0].message
        if not getattr(msg, "tool_calls", None):
            # AI 不调工具了，说明计划完成或有错误
            content = msg.content or ""
            if content:
                return content
            return "PlannerAgent finished without creating a plan."

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
                    # 只允许读操作
                    if _is_readonly(cmd):
                        result = ctx.ensure_sandbox().run(cmd, ctx.workspace)
                    else:
                        result = "Blocked: write/destructive commands not allowed in planning phase."
                elif name == "plan" and args.get("action") == "create":
                    # 规划完成，直接返回
                    plan_result = ctx.plan.create(args.get("tasks", []))
                    if plan_result.startswith("Plan created"):
                        ctx.plan.approve()
                        return plan_result
                    return plan_result
                else:
                    result = execute_tool(name, args, ctx.workspace, ctx)
                print(f"  [Planner] {name}: {str(result)[:200]}", flush=True)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)[:2000]})

    return "PlannerAgent reached max steps."


def _is_readonly(cmd: str) -> bool:
    """判断 shell 命令是否为只读操作。"""
    readonly = ["ls", "cat", "grep", "find", "head", "tail", "wc", "echo", "pwd",
                "which", "file", "sort", "uniq", "cut", "diff", "stat", "du", "df",
                "python --version", "python3 --version", "pip list", "pip show",
                "type", "printenv", "env", "history"]
    cmd_stripped = cmd.strip().split("|")[0].strip().split()[0] if cmd.strip() else ""
    return cmd_stripped in readonly or cmd.startswith("python -c ")


def _env_facts() -> str:
    import platform
    return (f"Environment: {platform.system()} {platform.release()} "
            f"| Python {platform.python_version()}")