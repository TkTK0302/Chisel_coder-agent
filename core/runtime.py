"""ExecutionContext：主循环运行时上下文，聚合各领域模块。

把感知/推理/执行/反思各模块挂到同一个上下文对象上，主循环只通过它调用，
实现模块间解耦 —— "事件驱动"的最小形态：工具结果即事件，回填给推理层。

依赖方向：agent -> tools -> 领域模块；领域模块只依赖 core，不反向 import agent。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core import ask as ask_mod
from core import context as ctx_mod
from core import loop_guard
from core import plan as plan_mod
from core.security_analyzer import SecurityAnalyzer


@dataclass
class ExecutionContext:
    workspace: str
    interactive: bool
    client: Any
    # 认知部件
    context: Any = ctx_mod
    loop: loop_guard.LoopGuard = None
    mistake: loop_guard.MistakeTracker = None
    plan: plan_mod.PlanTracker = None
    ask: Callable[[str], str] = None
    confirm_dangerous: Callable[[str], bool] = None
    security: SecurityAnalyzer = None
    # 执行环境（P3 懒加载）
    sandbox_mode: str = "auto"
    sandbox_image: str | None = None
    use_rag: bool = True
    sandbox: Any = None
    terminal: Any = None
    # 工具链（P4 接入）
    git: Any = None
    ast: Any = None
    # 当前任务描述（用于快照命名）
    task: str = ""
    # 检索（P5 接入）
    rag: Any = None

    def pinned(self) -> int:
        """消息前几条永不删：system / 计划占位 / 用户任务。"""
        return 3

    # --- 懒加载部件（首次使用才初始化，避免每个任务都拉起 Docker/索引） ----

    def ensure_sandbox(self):
        """创建沙盒（默认 auto：优先 Docker，失败降级宿主）。"""
        if self.sandbox is None:
            from env.sandbox import Sandbox
            import os
            image = self.sandbox_image or os.environ.get("CHISEL_SANDBOX_IMAGE", "python:3.12-slim")
            self.sandbox = Sandbox(self.workspace, self.sandbox_mode, image=image)
        return self.sandbox

    def ensure_terminal(self):
        """创建终端管理器（依赖沙盒）。"""
        if self.terminal is None:
            from env.terminal import TerminalManager

            self.terminal = TerminalManager(self.workspace, self.ensure_sandbox())
        return self.terminal

    def ensure_git(self):
        """创建 gitops（版本安全网）。"""
        if self.git is None:
            from gitops import GitOps

            self.git = GitOps(self.workspace)
        return self.git

    def ensure_ast(self):
        """创建 AST 符号索引（代码导航）。"""
        if self.ast is None:
            from perception.ast_index import ASTIndex

            self.ast = ASTIndex(self.workspace)
        return self.ast

    def ensure_rag(self):
        """创建混合检索（BM25 + 可选向量）。"""
        if self.rag is None:
            from rag.hybrid import HybridIndex

            self.rag = HybridIndex(self.workspace, client=self.client, enabled=self.use_rag)
        return self.rag


def build_runtime(
    workspace: str,
    client,
    interactive: bool = False,
    confirm_dangerous: Callable[[str], bool] | None = None,
    loop_soft: int = 3,
    loop_hard: int = 5,
    mistake_soft: int = 3,
    mistake_hard: int = 5,
    sandbox_mode: str = "auto",
    sandbox_image: str | None = None,
    use_rag: bool = True,
    **kwargs,
) -> ExecutionContext:
    """按参数拼装 ExecutionContext。懒加载模块（沙盒/RAG/AST）首次使用时才初始化。"""
    ctx = ExecutionContext(
        workspace=workspace,
        interactive=interactive,
        client=client,
        loop=loop_guard.LoopGuard(loop_soft, loop_hard),
        mistake=loop_guard.MistakeTracker(mistake_soft, mistake_hard),
        plan=plan_mod.PlanTracker(),
        ask=ask_mod.make_ask(interactive),
        confirm_dangerous=confirm_dangerous or (lambda cmd: False),
        security=SecurityAnalyzer(interactive=interactive, client=client, workspace=workspace),
        sandbox_mode=sandbox_mode,
        sandbox_image=sandbox_image,
        use_rag=use_rag,
    )
    for k, v in kwargs.items():
        if v is not None:
            setattr(ctx, k, v)
    return ctx
