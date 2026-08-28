"""主动提问：agent 在信息不足或需求模糊时向用户澄清（Human-in-the-Loop）。

仅交互模式可用；非交互模式返回提示，让模型基于已有信息做合理假设。
"""
from __future__ import annotations

import sys

from tools import register_tool

ASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "Ask the user a question when you need clarification or additional information. "
                       "Use this when the task requirements are ambiguous, when you need to choose "
                       "between approaches, or when required information (API keys, preferences) is missing. "
                       "Only available in interactive mode; in non-interactive mode, make a reasonable "
                       "assumption and document it in the final summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的问题"},
            },
            "required": ["question"],
        },
    },
}


def make_ask(interactive: bool):
    """返回一个 ask(question) -> str 函数。"""

    def ask(question: str) -> str:
        if not interactive:
            return (
                "当前是非交互模式，无法询问用户。请基于已有信息做出合理假设并继续，"
                "或在最终总结中说明你的假设。"
            )
        print(f"\n🤔  agent 提问：{question}")
        try:
            ans = input("   你的回答> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "用户未回答（输入中断）。请基于已有信息做出合理假设并继续。"
        return f"用户的回答：{ans}"

    return ask


def _handle_ask_user(ctx, args: dict) -> str:
    return ctx.ask(args.get("question", ""))


register_tool(ASK_SCHEMA, _handle_ask_user)
