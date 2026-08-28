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

SYSTEM_PROMPT = """你是一个编程智能体（coding agent），在一个工作目录里自主完成用户交给你的编程任务。

{env}

{repo_map}

你可以反复调用以下工具，直到任务完成：
- bash：执行 shell 命令（ls 看目录、cat 看文件、grep 搜索、python 运行代码、git 等）
- read_file：读取一个文件
- write_file：新建文件或整体覆盖文件
- edit_file：精确替换文件中的一小段（SEARCH/REPLACE，支持用 "..." 行省略中间代码）
{tool_hints}

工作方式（务必遵守）：
1. 先 bash ls 看目录结构，再 read_file 读相关文件，不要凭空猜文件内容。
2. 小范围修改用 edit_file，其 original_lines 必须与文件现有内容逐字符一致（含缩进）。
   若匹配失败，用 read_file 重新看真实内容再试。
3. 新建文件或需要大改时用 write_file。
4. **先思考再行动**：每次调用工具前，先用一小段话说明你的思考——当前假设、要做什么、
   预期结果。不要无目的地试错。
5. **验证闭环**：任何修改完成后，必须用 bash 运行测试/验证命令确认效果，禁止"改完就走"。
6. 任务完成后，用一段简短文字总结你做了什么，之后不要再调用任何工具。

{memory_section}
"""


def _env_facts() -> str:
    return (
        f"运行环境：{platform.system()} {platform.release()} | Python {platform.python_version()}"
        f" | 工作目录 {os.getcwd()}"
    )


def _tool_hints() -> str:
    """根据实际注册的工具，生成可选的工具用法提示（避免提示不存在的工具）。"""
    names = set(available_tool_names())
    hints = []
    if "plan" in names:
        hints.append("- 任务开始先思考，也可先用 plan 工具把任务拆成子任务清单，每完成一步更新进度。")
    if "rag_search" in names:
        hints.append("- 定位代码优先用 rag_search（语义检索），比盲目 grep 高效。")
    if "code_navigate" in names:
        hints.append("- 查函数/类定义、引用位置用 code_navigate。")
    if "web_fetch" in names:
        hints.append("- 需要查最新文档/API 说明时用 web_fetch 抓取网页。")
    if "web_search" in names:
        hints.append("- 需要搜索最新资料或 Stack Overflow 时用 web_search。")
    if "terminal" in names:
        hints.append("- 长驻进程（如 web server）用 terminal 启动；短命令用 bash。")
    if "git" in names:
        hints.append("- 可用 git 工具查看状态/提交/撤销本会话的修改。")
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
    ):
        self.client = client
        self.workspace = workspace
        self.max_steps = max_steps
        self.max_context_tokens = max_context_tokens
        self.sandbox_mode = sandbox_mode
        self.use_rag = use_rag
        self.max_loops = max_loops
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
