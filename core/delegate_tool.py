"""Delegate tool: spawn sub-agents for task execution (OpenHands-style).

When the Planning Agent calls delegate(task), a sub-agent is spawned as a
separate conversation with its own LLM session. The sub-agent has full access
to all tools (read/write/execute). The delegate call blocks until the sub-agent
completes, then returns the result to the parent.

Architecture reference: OpenHands DelegateExecutor + LocalConversation

Q6/Q7/Q10 improvements:
  - Sub-agents can escalate overly complex tasks back to the Planner
  - Full results saved to .chisel/sub_agent_results/ for later inspection
  - Sub-agent timeout triggers one auto-retry with focused prompt
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from tools import all_tools, execute_tool, register_tool
from core.context import truncate_output

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

# Q6: escalate 工具 —— 子 Agent 向上报告"任务需要进一步拆分"
ESCALATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": "Request the Planner to re-decompose this task. "
                       "Use this ONLY when the task is too complex for a single sub-agent "
                       "to complete within the step limit. Provide a clear reason and "
                       "suggested subtask breakdown.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this task needs further decomposition "
                                   "(e.g., 'involves 5+ files across multiple modules')",
                },
                "suggested_subtasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Suggested breakdown into smaller subtasks",
                },
            },
            "required": ["reason"],
        },
    },
}

# Track active sub-agents (for cleanup)
_active_sub_agents: list[dict] = []


def run_sub_agent(task: str, workspace: str, client, ctx, max_retries: int = 1) -> str:
    """Run a sub-agent with its own conversation loop.

    Returns the final answer from the sub-agent.

    Q10: Timeout auto-retries once with a focused prompt.
    Q7:  Full result saved to .chisel/sub_agent_results/.
    """
    from agent import build_system_prompt
    from llm import LLMClient

    for attempt in range(max_retries + 1):
        result = _run_sub_agent_loop(task, workspace, client, ctx)
        if "Sub-agent reached max steps" not in result:
            return result
        # Q10: 超时重试，给聚焦提示
        if attempt < max_retries:
            task = (
                f"{task}\n\n"
                "注意：上次执行超时（30 步）。请跳过不必要的探索步骤，"
                "直接聚焦核心修改和验证，用更少的步骤完成任务。"
            )
    return result


def _run_sub_agent_loop(task: str, workspace: str, client, ctx) -> str:
    """Internal: single sub-agent execution loop."""
    from agent import build_system_prompt
    from llm import LLMClient

    sub_agent_id = f"sub_{len(_active_sub_agents) + 1}"

    messages = [
        {"role": "system", "content": build_system_prompt(workspace, "")},
        {"role": "user", "content": task},
    ]

    info = {"id": sub_agent_id, "task": task, "step": 0}
    _active_sub_agents.append(info)

    milestones = []

    try:
        for step in range(1, 30):
            info["step"] = step
            try:
                resp = client.chat(messages, all_tools())
            except Exception:
                time.sleep(1)
                continue

            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                _save_full_result(sub_agent_id, final, workspace)
                return final

            messages.append(_assistant_msg(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                if name == "delegate":
                    result = "Cannot delegate from a sub-agent. Complete the task directly."
                elif name == "escalate":
                    # Q6: escalate 由 execute_tool 处理，子 Agent 可以调用
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        result = "Invalid JSON arguments."
                    else:
                        result = execute_tool(name, args, workspace, ctx)
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        result = "Invalid JSON arguments."
                    else:
                        result = execute_tool(name, args, workspace, ctx)
                        # 收集里程碑
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
                                 "content": truncate_output(
                                     str(result), save_dir=workspace, tool_prefix=name)})

            if step % 3 == 0 and milestones:
                for m in milestones[-2:]:
                    print(f"     {m}", flush=True)

        return "Sub-agent reached max steps."
    finally:
        if info in _active_sub_agents:
            _active_sub_agents.remove(info)


def _save_full_result(sub_agent_id: str, result: str, workspace: str) -> None:
    """Q7: 将子 Agent 完整结果保存到文件，避免截断丢失关键信息。"""
    if not result:
        return
    save_dir = Path(workspace) / ".chisel" / "sub_agent_results"
    save_dir.mkdir(parents=True, exist_ok=True)
    filepath = save_dir / f"{sub_agent_id}_result.txt"
    filepath.write_text(result, encoding="utf-8")


def _handle_delegate(ctx, args: dict) -> str:
    """Handle delegate tool call from the Planning Agent."""
    task = args.get("task", "")
    if not task:
        return "delegate: task is required."

    result = run_sub_agent(task, ctx.workspace, ctx.client, ctx)
    return f"Sub-agent result:\n{result}"


def _handle_escalate(ctx, args: dict) -> str:
    """Q6: Handle escalate tool call from a sub-agent.

    Returns a structured JSON with escalated=true so the Planner can
    identify this result and re-decompose the task.
    """
    reason = args.get("reason", "")
    suggested = args.get("suggested_subtasks", [])
    return json.dumps({
        "escalated": True,
        "reason": reason,
        "suggested_subtasks": suggested,
    }, ensure_ascii=False)


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
register_tool(ESCALATE_SCHEMA, _handle_escalate)  # Q6: 注册 escalate