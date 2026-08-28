"""编程智能体入口 + 主循环（agent loop）。

设计来源：
  - 主循环结构来自 Aider 的 coders/base_coder.py：
      run() -> run_one() -> send_message() -> send() -> models.send_completion()
    本项目把这条链压成一个 Agent.run()，去掉 Aider 的 UI/仓库/语法检查等无关模块。
  - 「模型不调工具 = 任务结束」的循环终止条件，来自 tool_choice="auto" 的语义；
    max_steps 是 Aider max_reflections 思路的移植，用于兜底防死循环。
  - 每步打印工具调用与结果（可见性），来自 Cline「每改一处都可见」的思路。

考核要求与代码落点：
  - 对话历史与上下文管理：messages 列表 + estimate_messages_tokens 超限截断
  - 工具的定义与本地执行：tools.py 的 TOOLS + execute_tool
  - 模型输出的解析：解析 message.tool_calls + json.loads(arguments) 的容错
  - 循环终止条件：无 tool_calls 即结束 + max_steps 兜底
  - 错误处理：LLM 调用重试 + 工具结果回填 + 坏 JSON 回填让模型自纠
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Windows 默认控制台是 GBK 编码，print/读取 中文或 emoji 会抛 UnicodeEncodeError；
# 统一把 stdin/stdout 切到 UTF-8，避免在不同终端下崩。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from llm import LLMClient
from memory import add_memory, load_memory
from tools import TOOLS, execute_tool

# --- system prompt ---------------------------------------------------------

SYSTEM_PROMPT = """你是一个编程智能体（coding agent），在一个工作目录里自主完成用户交给你的编程任务。

你可以反复调用以下工具，直到任务完成：
- bash：执行 shell 命令（ls 看目录、cat 看文件、grep 搜索、python 运行代码、git 等）
- read_file：读取一个文件
- write_file：新建文件或整体覆盖文件
- edit_file：精确替换文件中的一小段（SEARCH/REPLACE）

工作方式（务必遵守）：
1. 先 bash ls 看目录结构，再 read_file 读相关文件，不要凭空猜文件内容。
2. 小范围修改用 edit_file，其 original_lines 必须与文件现有内容逐字符一致（含缩进）。
   若匹配失败，用 read_file 重新看真实内容再试。
3. 新建文件或需要大改时用 write_file。
4. 改完后用 bash 运行代码/测试来验证结果。
5. 任务完成后，用一段简短文字总结你做了什么，之后不要再调用任何工具。

{memory_section}
"""


def build_system_prompt(workspace: str) -> str:
    memory = load_memory(workspace)
    memory_section = f"用户的长期偏好（来自 MEMORY.md，请遵守）：\n{memory}\n" if memory else ""
    return SYSTEM_PROMPT.format(memory_section=memory_section)


# --- 危险命令确认（来源：OpenHands confirmation mode） ---------------------


def confirm_dangerous(command: str) -> bool:
    print(f"\n  ⚠️  检测到危险命令：{command}")
    # 非交互环境（管道/CI）没有 stdin，input() 会抛 EOFError；此时默认拒绝，安全第一。
    if not sys.stdin.isatty():
        print("  （非交互环境，自动拒绝执行）")
        return False
    try:
        ans = input("  确认执行吗？(y/n) ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


# --- Agent 主循环 ----------------------------------------------------------


class Agent:
    def __init__(
        self,
        client: LLMClient,
        workspace: str,
        max_steps: int = 50,
        max_context_tokens: int = 60000,
    ):
        self.client = client
        self.workspace = workspace
        self.max_steps = max_steps
        self.max_context_tokens = max_context_tokens

    def run(self, task: str) -> str:
        """跑一个任务，返回最终回答。"""
        messages = [
            {"role": "system", "content": build_system_prompt(self.workspace)},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            print(f"\n[步骤 {step}] 请求模型中…", flush=True)
            resp = self._chat_with_retry(messages)
            msg = resp.choices[0].message

            # ---- 循环终止条件：模型不再调用工具，只返回文本 ----
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                print(f"\n{'='*60}\n✅ 任务完成（共 {step} 步）\n{'='*60}\n{final}\n")
                return final

            # 回填 assistant 消息（含 tool_calls），再逐条回填工具结果
            messages.append(self._assistant_msg(msg))

            for tc in msg.tool_calls:
                name = tc.function.name
                # ---- 模型输出解析：arguments 是 JSON 字符串，可能坏 ----
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    result = "工具参数不是合法 JSON，请重新输出正确的 JSON 参数。"
                else:
                    print(f"\n🔧 [{step}] 调用工具 {name}: {json.dumps(args, ensure_ascii=False)[:200]}")
                    result = execute_tool(name, args, self.workspace, confirm_dangerous)
                    # 可见性：把执行结果也打出来，便于观察 agent 每一步在干什么
                    print(f"   ↳ {result[:500]}")

                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            # ---- 上下文管理：超限时丢弃最旧的对话（保留 system 与最近内容） ----
            self._trim_context(messages)

        print("\n⚠️ 达到最大步数，已强制停止。")
        return ""

    # --- 内部方法 ----------------------------------------------------------

    def _chat_with_retry(self, messages):
        """LLM 调用 + 重试（来源：Aider send_message 的指数退避重试）。"""
        delay = 0.5
        last_err = None
        for _ in range(3):
            try:
                return self.client.chat(messages, TOOLS)
            except Exception as e:  # 网络/限流/服务端错误
                last_err = e
                print(f"  ⚠️ LLM 调用失败（{type(e).__name__}），{delay}s 后重试…")
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"LLM 连续调用失败: {last_err}")

    @staticmethod
    def _assistant_msg(msg) -> dict:
        """把响应里的 assistant 消息转成可回填的 dict（保留 tool_calls 的 id）。"""
        return {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }

    def _trim_context(self, messages):
        """超限时从最早开始删消息（保留 system 在第 0 位）。"""
        while (
            len(messages) > 2
            and self.client.estimate_messages_tokens(messages) > self.max_context_tokens
        ):
            messages.pop(1)


# --- 入口 -------------------------------------------------------------------


def _load_env_file(path: str = ".env") -> dict:
    """读取 .env 文件（简单 KEY=VALUE 解析，不引入 python-dotenv 依赖）。"""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _load_api_key(args) -> str:
    """API key 读取优先级：命令行参数 > 环境变量 > .env 文件（未入库）。

    考核要求「凭据通过环境变量或未入库的配置文件提供」，这里 .env 已被
    .gitignore 排除，不会提交到仓库。
    """
    if args.api_key:
        return args.api_key
    for var in ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"):
        val = os.environ.get(var)
        if val:
            return val
    env_file = _load_env_file()
    for var in ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        if env_file.get(var):
            return env_file[var]
    return ""


def main():
    parser = argparse.ArgumentParser(description="编程智能体（coding agent）")
    parser.add_argument("task", nargs="?", help="要完成的任务描述（省略则进入交互模式）")
    parser.add_argument("--base-url", default="https://api.deepseek.com", help="OpenAI 兼容 API 地址")
    parser.add_argument("--model", default="deepseek-chat", help="模型名")
    parser.add_argument("--api-key", default="", help="API key（建议用环境变量 LLM_API_KEY）")
    parser.add_argument("--workspace", default=".", help="工作目录（agent 在其中读写文件、执行命令）")
    parser.add_argument("--memory", default="", help="记住一条用户偏好，写入 MEMORY.md")
    parser.add_argument("--max-steps", type=int, default=50, help="最大步数（防死循环）")
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)

    # --memory 参数：存偏好（来源：MiMo/OpenCode 的 /memory 思路）
    if args.memory:
        print(add_memory(workspace, args.memory))
        if not args.task:
            return

    api_key = _load_api_key(args)
    if not api_key:
        print("❌ 未找到 API key。请设置环境变量，例如：")
        print('   $env:LLM_API_KEY="sk-..."   （PowerShell）')
        print("   或 export LLM_API_KEY=sk-... （bash）")
        return

    client = LLMClient(base_url=args.base_url, model=args.model, api_key=api_key)
    agent = Agent(client, workspace=workspace, max_steps=args.max_steps)

    if args.task:
        agent.run(args.task)
        return

    # 交互模式：反复读用户输入
    print(f"工作目录：{workspace}\n模型：{args.model}\n输入任务，或输入 /quit 退出。")
    while True:
        try:
            task = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task in ("/quit", "/exit", "quit"):
            break
        agent.run(task)


if __name__ == "__main__":
    main()
