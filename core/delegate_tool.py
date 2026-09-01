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

    messages = [
        {"role": "system", "content": build_system_prompt(workspace, "")},
        {"role": "user", "content": task},
    ]

    info = {"id": sub_agent_id, "task": task, "step": 0}
    _active_sub_agents.append(info)

    # 收集执行过程中的关键里程碑
    milestones = []

    try:
        for step in range(1, 30):
            info["step"] = step
            try:
                resp = client.chat(messages, all_tools())
            except Exception as e:
                time.sleep(1)
                continue

            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                return final

            messages.append(_assistant_msg(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                if name == "delegate":
                    result = "Cannot delegate from a sub-agent. Complete the task directly."
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        result = "Invalid JSON arguments."
                    else:
                        result = execute_tool(name, args, workspace, ctx)
                        # 收集里程碑（只记录有意义的事件，不打印每步细节）
                        if name == "code_navigate" and "definition" == args.get("action"):
                            symbol = args.get("symbol", "")
                            if symbol:
                                milestones.append(f"📍 定位 {symbol}")
                        elif name == "edit_file":
                            path = args.get("path", "")
                            if "Created" in result:
                                milestones.append(f"✏️ 创建 {path}")
                            elif "Edited" in result:
                                milestones.append(f"✏️ 修改 {path}")
                        elif name == "bash":
                            cmd = args.get("command", "")
                            if "test" in cmd or "pytest" in cmd:
                                if "exit code 0" in result:
                                    milestones.append("✅ 测试通过")
                                else:
                                    milestones.append("⚠️ 测试失败，分析中...")
                            elif "pip install" in cmd:
                                milestones.append("📦 安装依赖...")
                        elif name == "attempt_completion":
                            milestones.append("✅ 完成")

                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": str(result)[:3000]})

            # 每 3 步打印一次里程碑汇总
            if step % 3 == 0 and milestones:
                for m in milestones[-2:]:  # 只打印最近 2 个
                    print(f"     {m}", flush=True)

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