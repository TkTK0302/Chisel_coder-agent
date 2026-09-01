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
                    "description": (
                        "任务完成总结，用中文。格式要求：\n"
                        "1. 标题和下方内容之间不要有多余空行，标题后直接换行接内容\n"
                        "2. 使用紧凑格式：每个小节标题后紧跟内容，不同小节之间只空一行\n"
                        "3. 示例格式：\n"
                        "修改内容\n"
                        "- 在 compound_interest 函数签名中添加了 tax_rate: float = 0 参数\n"
                        "- 在返回值中扣除了税费\n"
                        "\n"
                        "验证结果\n"
                        "- 测试全部通过 ✓\n"
                        "- 文件其他部分未受影响 ✓"
                    ),
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