"""attempt_completion 工具：AI 显式声明任务完成并给出总结。

设计来源（Cline 的 attempt_completion 工具思路）：
  相比"无 tool_calls 即完成"的隐式终止，显式调此工具更可靠 ——
  AI 明确说"我做完了"，并提供总结。主循环收到此工具调用后立即终止，
  不再执行同一批中的其它工具调用。
"""
from __future__ import annotations

from tools import register_tool

COMPLETION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "attempt_completion",
        "description": "Signal that the task is complete and provide a final summary of what was done. "
                       "Call this tool when you have verified that the task requirements are met "
                       "(e.g., tests pass, code compiles, changes are correct). "
                       "After calling this tool, no further tool calls will be possible in this task. "
                       "Provide a detailed result describing what was accomplished, what changes were made, "
                       "and any verification results (test output, fixes, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "任务完成总结，用中文说明你做了什么、结果如何",
                }
            },
            "required": ["result"],
        },
    },
}


def _handle_completion(ctx, args: dict) -> str:
    """把总结存到 ctx 中，主循环检测到后终止。"""
    result = args.get("result", "任务完成。")
    ctx._completion_result = result
    return result


register_tool(COMPLETION_SCHEMA, _handle_completion)