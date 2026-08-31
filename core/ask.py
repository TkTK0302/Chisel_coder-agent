"""主动提问：agent 在信息不足或需求模糊时向用户澄清（Human-in-the-Loop）。

支持两种交互模式：
  - CLI 模式：直接 input() 等待用户输入
  - Eel 桌面模式：通过文件 IPC 将问题推送到前端，用户点击按钮选择
"""
from __future__ import annotations

import sys
import os

from tools import register_tool

ASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "向用户提问以澄清需求或获取决策。当任务需求模糊、需要选择方案、或缺少信息时使用。"
                       "可同时提供多个选项让用户选择。非交互模式下请基于已有信息合理假设。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的问题，用中文"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选项列表，如 ['是', '否'] 或 ['方案A', '方案B', '方案C']，省略则默认 yes/no",
                },
            },
            "required": ["question"],
        },
    },
}


def make_ask(interactive: bool):
    """返回一个 ask(question, options) -> str 函数。"""

    def ask(question: str, options: list[str] | None = None) -> str:
        if not interactive:
            return "当前非交互模式，无法询问。请基于已有信息合理假设。"

        # 检测是否在 Eel 桌面模式（通过环境变量）
        if os.environ.get("CHISEL_DESKTOP"):
            from core.user_input import ask_question
            return ask_question(
                os.environ.get("CHISEL_WORKSPACE", "."),
                question,
                options,
            )

        # CLI 模式：直接 input()
        print(f"\n🤔  agent 提问：{question}")
        if options:
            print(f"   选项：{' / '.join(options)}")
        try:
            ans = input("   你的选择> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "用户未回答。请合理假设。"
        return f"用户选择：{ans}"

    return ask


def _handle_ask_user(ctx, args: dict) -> str:
    return ctx.ask(
        args.get("question", ""),
        args.get("options"),
    )


register_tool(ASK_SCHEMA, _handle_ask_user)