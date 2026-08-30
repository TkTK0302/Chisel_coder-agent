"""Delegate tool: spawn sub-agents for task execution (OpenHands-style).

When the Planning Agent calls delegate(task), a sub-agent is spawned as a
separate conversation with its own LLM session. The sub-agent has full access
to all tools (read/write/execute). The delegate call blocks until the sub-agent
completes, then returns the result to the parent.

Architecture reference: OpenHands DelegateExecutor + LocalConversation
"""
from __future__ import annotations

import json
import sys
import time

from tools import all_tools, execute_tool, register_tool

DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": "Delegate a task to a sub-agent. The sub-agent has full read/write/execute "
                       "access and will work autonomously until the task is complete. "
                       "Use this to execute individual tasks from the plan. "
                       "The delegate call blocks until the sub-agent finishes.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task description for the sub-agent"},
                "agent_id": {"type": "string", "description": "Optional identifier for the sub-agent"},
            },
            "required": ["task"],
        },
    },
}

# Track active sub-agents (for cleanup)
_active_sub_agents: list[dict] = []


def run_sub_agent(task: str, workspace: str, client, ctx) -> str:
    """Run a sub-agent with its own conversation loop.

    Returns the final answer from the sub-agent.
    """
    from agent import build_system_prompt
    from llm import LLMClient

    sub_agent_id = f"sub_{len(_active_sub_agents) + 1}"
    print(f"\n  [Sub-agent {sub_agent_id}] Starting task: {task[:100]}...", flush=True)

    messages = [
        {"role": "system", "content": build_system_prompt(workspace)},
        {"role": "user", "content": task},
    ]

    info = {"id": sub_agent_id, "task": task, "step": 0}
    _active_sub_agents.append(info)

    try:
        for step in range(1, 30):
            info["step"] = step
            try:
                resp = client.chat(messages, all_tools())
            except Exception as e:
                print(f"    [Sub-agent {sub_agent_id}] LLM error: {e}", flush=True)
                time.sleep(1)
                continue

            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                print(f"    [Sub-agent {sub_agent_id}] Completed ({step} steps)", flush=True)
                return final

            messages.append(_assistant_msg(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                if name == "delegate":
                    # Prevent nested delegation
                    result = "Cannot delegate from a sub-agent. Complete the task directly."
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        result = "Invalid JSON arguments."
                    else:
                        result = execute_tool(name, args, workspace, ctx)
                        print(f"      [{step}] {name}: {str(result)[:200]}", flush=True)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": str(result)[:3000]})

        return "Sub-agent reached max steps."
    finally:
        if info in _active_sub_agents:
            _active_sub_agents.remove(info)


def _handle_delegate(ctx, args: dict) -> str:
    """Handle delegate tool call from the Planning Agent."""
    task = args.get("task", "")
    if not task:
        return "delegate: task is required."

    result = run_sub_agent(task, ctx.workspace, ctx.client, ctx)
    return f"Sub-agent result:\n{result}"


def cleanup_sub_agents() -> None:
    """Clean up any remaining sub-agents."""
    _active_sub_agents.clear()


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


register_tool(DELEGATE_SCHEMA, _handle_delegate)