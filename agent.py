"""编程智能体入口 + 主循环（agent loop）。

设计来源：
  - 主循环结构来自 Aider 的 coders/base_coder.py：run -> run_one -> send_message
    -> send -> models.send_completion。本项目压缩为 Agent.run()。
  - 「模型不调工具 = 任务结束」的循环终止条件，来自 tool_choice="auto" 语义；
    max_steps 是 Aider max_reflections 思路的移植，用于兜底防死循环。
  - 每步打印工具调用与结果（可见性），来自 Cline「每改一处都可见」思路。
  - 显式规划（plan 工具 + 每轮注入计划）来自 OpenHands PlannerAgent 思路；
    死循环检测与连续错误追踪（core/loop_guard）来自 Cline loop-detection；
    上下文压缩（core/context）来自 Cline basic-compaction 与 Aider ChatSummary。

考核要求与代码落点：
  - 对话历史与上下文管理：messages 列表 + core/context.compress_context 超限压缩
  - 工具的定义与本地执行：tools.py 的 all_tools() + execute_tool
  - 模型输出的解析：解析 message.tool_calls + json.loads(arguments) 的容错
  - 循环终止条件：无 tool_calls 即结束 + max_steps 兜底
  - 错误处理：LLM 调用重试 + 工具结果回填 + 坏 JSON 回填让模型自纠
"""

from __future__ import annotations

import argparse
import json
import os
import platform
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

from core.runtime import build_runtime
from core.dual_controller import DualController
import core.completion  # noqa: F401  （注册 attempt_completion 工具）
import env.terminal  # noqa: F401  （import 副作用：注册 terminal 工具）
import gitops  # noqa: F401  （注册 git 工具）
import perception.ast_index  # noqa: F401  （注册 code_navigate 工具）
import perception.web  # noqa: F401  （注册 web_fetch 工具）
import perception.web_search  # noqa: F401  （注册 web_search 工具）
import rag.hybrid  # noqa: F401  （注册 rag_search 工具）
from llm import LLMClient
from memory import add_memory, load_memory
from tools import all_tools, available_tool_names, execute_tool

# --- system prompt ---------------------------------------------------------

SYSTEM_PROMPT = """You are Chisel, an AI coding agent that works autonomously in a workspace directory to complete programming tasks.

{env}

{repo_map}

You have access to the following tools to read, write, and execute code:
- bash: execute shell commands (ls, cat, grep, git, python, etc.)
- read_file: read the content of a text file
- write_file: create a new file or overwrite an existing one
- edit_file: apply a precise SEARCH/REPLACE edit to a file
{tool_hints}

Working guidelines (follow strictly):
1. Explore first: use bash to list the directory, then read relevant files. Never guess file contents.
2. For small changes, use edit_file. Its original_lines must match the file content exactly (including whitespace). If the match fails, read the file again to see the actual content.
3. For new files or large rewrites, use write_file.
4. **Think before you act**: before each tool call, briefly explain your reasoning — what you assume, what you intend to do, and what you expect to happen.
5. **Verify your work**: after any modification, run tests or validation commands to confirm correctness. Never assume changes work without verification.
6. At the start of a task, use plan to decompose it into subtasks; update progress as you go.
7. For large projects, prioritize rag_search / code_navigate to locate relevant code before editing.
8. When the task is complete, call attempt_completion with a summary of what was done and the verification results.

IMPORTANT: Always include tool calls in your response until the task is completed. A response without tool calls will be considered as the final answer.

{memory_section}
"""


def _env_facts() -> str:
    return (
        f"Environment: {platform.system()} {platform.release()} | Python {platform.python_version()}"
        f" | Working directory: {os.getcwd()}"
    )


def _tool_hints() -> str:
    """Generate dynamic tool usage hints based on registered tools (avoids mentioning unavailable tools)."""
    names = set(available_tool_names())
    hints = []
    if "plan" in names:
        hints.append("- plan: decompose the task into subtasks at the start, update progress as you complete each step.")
    if "rag_search" in names:
        hints.append("- rag_search: search the codebase semantically before blindly grepping or reading entire files.")
    if "code_navigate" in names:
        hints.append("- code_navigate: find function/class definitions, references, or list project symbols.")
    if "web_fetch" in names:
        hints.append("- web_fetch: fetch a web page to look up documentation or API references.")
    if "web_search" in names:
        hints.append("- web_search: search the web for recent information, tutorials, or Stack Overflow solutions.")
    if "terminal" in names:
        hints.append("- terminal: start/stream/kill long-running processes (web servers, compilers). Use bash for short commands.")
    if "git" in names:
        hints.append("- git: status, commit, diff, undo (safely revert agent-generated commits).")
    if "attempt_completion" in names:
        hints.append("- attempt_completion: call this when verification passes and the task is complete.")
    return "\n".join(hints)


def build_system_prompt(workspace: str) -> str:
    memory = load_memory(workspace)
    memory_section = f"用户的长期偏好（来自 MEMORY.md，请遵守）：\n{memory}\n" if memory else ""
    from perception.repo_map import get_repo_map
    repo_map = get_repo_map(workspace)
    return SYSTEM_PROMPT.format(
        memory_section=memory_section,
        env=_env_facts(),
        repo_map=repo_map,
        tool_hints=_tool_hints(),
    )


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
        sandbox_mode: str = "auto",
        use_rag: bool = True,
        max_loops: int = 5,
        plan_mode: str = "auto",  # cline | openhands | auto
    ):
        self.client = client
        self.workspace = workspace
        self.max_steps = max_steps
        self.max_context_tokens = max_context_tokens
        self.sandbox_mode = sandbox_mode
        self.use_rag = use_rag
        self.max_loops = max_loops
        self.plan_mode = plan_mode
        self.ctx = None

    def run(self, task: str) -> str:
        """跑一个任务，返回最终回答。"""
        ctx = build_runtime(
            self.workspace,
            self.client,
            interactive=sys.stdin.isatty(),
            confirm_dangerous=confirm_dangerous,
            loop_hard=self.max_loops,
            mistake_hard=self.max_loops,
            sandbox_mode=self.sandbox_mode,
            use_rag=self.use_rag,
        )
        self.ctx = ctx

        messages = [
            {"role": "system", "content": build_system_prompt(self.workspace)},
            {"role": "system", "content": ""},  # 计划占位，每轮 inject 改写
            {"role": "user", "content": task},
        ]
        ctx.plan.inject(messages)

        # ---- 计划模式调度：Cline（小项目） vs OpenHands（大项目） ----
        import core.project_detector as detector

        mode = self.plan_mode
        if mode == "auto":
            mode = detector.detect_mode(self.workspace)
            print(f"  [Plan mode: {mode.upper()}] {detector.describe(self.workspace)}", flush=True)

        if mode == "openhands":
            print(f"\n{'='*60}\n  OpenHands Dual-Agent Mode\n{'='*60}", flush=True)
            controller = DualController(self.workspace, self.client, ctx)
            return controller.run(task, self.max_steps)

        # Cline 模式：标准主循环（含审批环节）
        ctx.plan.mode = "cline"
        print(f"  [Plan mode: CLINE] Standard agent loop with plan approval.", flush=True)

        for step in range(1, self.max_steps + 1):
            # 发送前注入当前计划与循环/错误警告（此时上轮工具回合已闭合）
            self._pre_step_hook(messages, ctx)
            print(f"\n[步骤 {step}] 请求模型中…", flush=True)
            resp = self._chat_with_retry(messages)
            msg = resp.choices[0].message

            # ---- 循环终止条件：模型不再调用工具，只返回文本 ----
            if not getattr(msg, "tool_calls", None):
                final = msg.content or ""
                print(f"\n{'='*60}\n✅ 任务完成（共 {step} 步）\n{'='*60}\n{final}\n")
                self._cleanup(ctx)
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
                    result = execute_tool(name, args, self.workspace, ctx)
                    # 可见性：把执行结果也打出来，便于观察 agent 每一步在干什么
                    print(f"   ↳ {result[:500]}")

                # 死循环 / 连续错误追踪（元工具不参与计数）
                if name not in ("plan", "attempt_completion"):
                    ctx.loop.note_call(name, args)
                ctx.mistake.track(result)
                # 长结果截断后再回填，防止报错日志挤爆上下文
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": ctx.context.truncate_output(result)})

            # ---- 检测 attempt_completion（显式完成信号） ----
            if getattr(ctx, "_completion_result", None):
                final = ctx._completion_result
                print(f"\n{'='*60}\n✅ 任务完成（共 {step} 步）\n{'='*60}\n{final}\n")
                self._cleanup(ctx)
                return final

            # ---- 上下文管理：超限时整组压缩（确定性折叠 + 必要时 LLM 摘要） ----
            ctx.context.compress_context(
                messages, self.client, self.max_context_tokens, pinned=ctx.pinned()
            )

            if ctx.loop.should_abort() or ctx.mistake.should_abort():
                print("\n⚠️ 检测到疑似死循环或连续失败，已停止（工作区修改保留）。")
                self._cleanup(ctx)
                return "任务因疑似死循环/连续失败被提前停止。已完成的工作保留在工作区。"

        print("\n⚠️ 达到最大步数，已强制停止。")
        self._cleanup(ctx)
        return ""

    # --- 内部方法 ----------------------------------------------------------

    def _pre_step_hook(self, messages, ctx):
        """每轮发送前注入当前计划与循环/错误警告。

        关键约束：所有合成注入只允许在这里追加（此时上一轮工具回合已完整、
        消息队列干净）。绝不把合成消息插进 assistant(tool_calls) 与其 tool 结果之间。
        """
        ctx.plan.inject(messages)  # 原位改写 messages[1] 为当前计划
        for w in ctx.loop.drain_warnings() + ctx.mistake.drain_warnings():
            messages.append({"role": "user", "content": w})

    def _cleanup(self, ctx):
        """任务结束时清理运行环境（长驻进程等）。"""
        if getattr(ctx, "terminal", None) is not None:
            try:
                ctx.terminal.kill_all()
            except Exception:
                pass
        if getattr(ctx, "plan", None) is not None:
            ctx.plan.finalize()

    def _chat_with_retry(self, messages):
        """LLM 调用 + 重试（来源：Aider send_message 的指数退避重试）。"""
        delay = 0.5
        last_err = None
        for _ in range(3):
            try:
                return self.client.chat(messages, all_tools())
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
    """API key 读取优先级：命令行参数 > 环境变量 > .env 文件（未入库）。"""
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
    parser.add_argument("--max-loops", type=int, default=5, help="连续相同调用/连续错误的最大次数（触发中止）")
    parser.add_argument("--sandbox", choices=["docker", "host", "auto"], default="auto",
                        help="命令执行环境：docker=容器沙盒，host=宿主机，auto=优先 Docker 失败自动降级")
    parser.add_argument("--no-rag", action="store_true", help="禁用代码库语义检索（RAG）")
    parser.add_argument("--plan-mode", choices=["cline", "openhands", "auto"], default="auto",
                        help="规划模式：cline=同一 AI 两阶段，openhands=双 AI 规划+执行，auto=自动检测项目规模选择")
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
    agent = Agent(
        client,
        workspace=workspace,
        max_steps=args.max_steps,
        sandbox_mode=args.sandbox,
        use_rag=not args.no_rag,
        max_loops=args.max_loops,
        plan_mode=args.plan_mode,
    )

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
