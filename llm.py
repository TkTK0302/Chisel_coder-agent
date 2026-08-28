"""模型层：OpenAI 兼容的 tool calling 封装。

设计来源：Aider 的 models.py -> send_completion() 方法。
从 Aider 提取的核心思路：
  1. 用 tools=[{"type": "function", "function": {...}}] 把工具 schema 传给模型
  2. 用 tool_choice 控制模型是否/如何调用工具
  3. 从响应的 message.tool_calls 里解析模型想调用的工具名和参数

改造点（相比 Aider）：
  - Aider 用 litellm 封装 75+ 家 provider。本项目只用 OpenAI 兼容接口
    （DeepSeek / Moonshot / GLM / OpenRouter 网关），直接用 openai 官方库，
    更贴合考核「允许模型厂商 API 客户端库 + OpenAI 兼容网关」的边界。
  - Aider 强制 tool_choice 指向唯一的 replace_lines 工具；本项目有多个工具，
    用 tool_choice="auto" 让模型自己决定要不要调工具——这同时成为「循环终止」的信号：
    模型不调工具、只返回文本，就意味着任务结束。
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


class LLMClient:
    """对 OpenAI 兼容 chat.completions 接口的最小封装。"""

    def __init__(self, base_url: str, model: str, api_key: str):
        self.model = model
        # base_url 可切 DeepSeek(https://api.deepseek.com) 或网关(如 OpenRouter)
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        """发一次请求，返回原生响应对象（含 choices[0].message）。

        tool_choice="auto"：模型自己决定调不调工具。不调 = 只回文本 = 任务结束。
        """
        kwargs: dict[str, Any] = dict(model=self.model, messages=messages)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self.client.chat.completions.create(**kwargs)

    # --- 上下文管理 ---------------------------------------------------------
    # 来源：Aider base_coder.py 的 check_tokens() —— 发送前估算 token，超限则报错。
    # 这里做一个粗估（中文约 1 字/token，英文约 3 字符/token），用于超限截断。

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """粗略 token 估算，无外部依赖。

        中文汉字约 1 token/字，英文约 3~4 字符/token。对 tool calling 场景
        够用（只需判断是否逼近上限，不需要精确计费）。
        """
        cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
        other = len(text) - cjk
        return cjk + other // 3

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            total += self.estimate_tokens(json.dumps(m, ensure_ascii=False))
        return total
