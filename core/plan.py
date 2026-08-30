"""计划系统：显式任务拆解 + 进度追踪 + 依赖管理 + 审批。

两种模式共享同一个 PlanTracker：
  - Cline 模式（小项目）：同一 AI 先 Plan（只读+审批）后 Act（执行）
  - OpenHands 模式（大项目）：PlannerAgent 规划 + CodeActAgent 执行，双 AI

设计来源：
  - 状态机 + 依赖注入来自 OpenHands Plan/PlanTask/PlanProgress
  - 审批环节来自 Cline Plan/Act 模式
  - 自写实现，不依赖外部代码
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from tools import register_tool

TASK_STATUS = ("pending", "in_progress", "done", "blocked", "verified")


@dataclass
class PlanTask:
    id: str
    description: str
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)  # 前置任务 ID 列表
    notes: str = ""  # 完成时的备注


class PlanTracker:
    """计划追踪器，两种模式共用。

    Cline 模式流程：
      create(待审批) → approve() → AI 执行 → update()

    OpenHands 模式流程：
      PlannerAgent create(含依赖) → CodeActAgent 执行 → 遇阻 replan
    """

    def __init__(self):
        self.tasks: list[PlanTask] = []
        self.created = False
        self.approved = False  # Cline 审批标记
        self.mode: str = "single"  # single | multi

    # --- 创建与更新 ----------------------------------------------------------

    def create_empty(self) -> None:
        self.tasks = []
        self.created = True
        self.approved = False

    def create(self, tasks: list[dict]) -> str:
        """创建计划。tasks: [{id, description, status, depends_on}]"""
        cleaned: list[PlanTask] = []
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            desc = str(t.get("description", "")).strip()
            status = t.get("status", "pending")
            deps = [str(d).strip() for d in t.get("depends_on", []) if str(d).strip()]
            notes = str(t.get("notes", "")).strip()
            if status not in TASK_STATUS:
                return f"plan: status must be one of {TASK_STATUS}, got {status!r}"
            if not tid or not desc:
                return "plan: each task requires non-empty id and description"
            cleaned.append(PlanTask(id=tid, description=desc, status=status, depends_on=deps, notes=notes))
        # 校验依赖存在性
        ids = {t.id for t in cleaned}
        for t in cleaned:
            for dep in t.depends_on:
                if dep not in ids:
                    return f"plan: task {t.id} depends on unknown task {dep!r}"
        self.tasks = cleaned
        self.created = True
        self.approved = False
        return f"Plan created: {len(cleaned)} subtasks. Waiting for approval..."

    def approve(self) -> str:
        """用户批准计划（Cline 模式）。"""
        if not self.tasks:
            return "plan: no plan to approve."
        self.approved = True
        return "Plan approved."

    def update(self, tasks: list[dict]) -> str:
        """更新进度。"""
        if not self.tasks:
            return "plan: no plan yet, use action=create first."
        by_id = {t.id: t for t in self.tasks}
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            if tid not in by_id:
                existing = ", ".join(t.id for t in self.tasks)
                return f"plan: task {tid!r} not found. Existing tasks: {existing}"
            status = t.get("status")
            if status and status not in TASK_STATUS:
                return f"plan: status must be one of {TASK_STATUS}"
            if status:
                by_id[tid].status = status
            notes = t.get("notes", "")
            if notes:
                by_id[tid].notes = str(notes).strip()
        return "Plan updated."

    def note(self, task_id: str, notes: str) -> str:
        """给任务添加备注（OpenHands 风格）。"""
        if task_id not in {t.id for t in self.tasks}:
            return f"plan: task {task_id!r} not found."
        for t in self.tasks:
            if t.id == task_id:
                t.notes = notes
                return f"Notes added to task {task_id}."
        return ""

    def blocked_by(self) -> list[str]:
        """返回所有因依赖未满足而 blocked 的任务 ID。"""
        done_ids = {t.id for t in self.tasks if t.status in ("done", "verified")}
        return [t.id for t in self.tasks if t.status != "blocked"
                and any(d not in done_ids for d in t.depends_on)]

    # --- 渲染 --------------------------------------------------------------

    def to_text(self) -> str:
        if not self.tasks:
            return "Plan: not yet created. Use plan tool with action=create to break down the task."
        done = sum(1 for t in self.tasks if t.status in ("done", "verified"))
        total = len(self.tasks)
        lines = []
        for t in self.tasks:
            dep_str = f" ⛔ depends: {t.depends_on}" if t.depends_on else ""
            note_str = f"  → {t.notes}" if t.notes else ""
            lines.append(f"  [{t.status:>4}] {t.id}: {t.description}{dep_str}{note_str}")
        header = "Plan (update progress with plan action=update):"
        footer = f"Progress: {done}/{total}"
        phase = " [PLAN PHASE - read only]" if not self.approved else ""
        return f"{header}{phase}\n" + "\n".join(lines) + f"\n{footer}"

    def inject(self, messages: list[dict]) -> None:
        """把当前计划原位写回 messages[1]。"""
        while len(messages) < 2:
            messages.append({"role": "system", "content": ""})
        messages[1] = {"role": "system", "content": self.to_text()}

    def finalize(self) -> None:
        pass

    def to_dict(self) -> list[dict]:
        """OpenHands 模式：导出计划给 CodeActAgent 使用。"""
        return [
            {"id": t.id, "description": t.description, "status": t.status,
             "depends_on": t.depends_on, "notes": t.notes}
            for t in self.tasks
        ]


# --- plan 工具 schema --------------------------------------------------------

PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": "Create or update the task breakdown for the current assignment. "
                       "Call action=create at the start to decompose the task, "
                       "action=update to mark progress, "
                       "action=approve to confirm the plan (Cline mode). "
                       "Use depends_on to specify task dependencies. "
                       "The current plan is automatically injected into your context.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "approve", "note"]},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked", "verified"]},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "notes": {"type": "string"},
                        },
                        "required": ["id", "description"],
                    },
                },
                "task_id": {"type": "string", "description": "action=note 时指定任务 ID"},
                "notes": {"type": "string", "description": "action=note 时的备注内容"},
            },
            "required": ["action"],
        },
    },
}


def _handle_plan(ctx, args: dict) -> str:
    plan = ctx.plan
    action = args.get("action")
    tasks = args.get("tasks") or []

    if action == "create":
        result = plan.create(tasks)
        if not result.startswith("Plan created"):
            return result
        # 两种模式都走审批环节
        if plan.mode == "multi":
            # Multi mode: planner asks user, then proceeds
            if ctx.ask:
                summary = "\n".join(f"  [{t['status']}] {t['id']}: {t['description']}"
                                   for t in tasks)
                approval = ctx.ask(
                    f"Plan created with {len(tasks)} tasks. Approve and start execution?\n{summary}"
                )
                if approval and any(kw in approval.lower() for kw in ["no", "not", "dis", "reject", "revise"]):
                    return "Plan not approved. Please revise the plan."
                plan.approve()
                return "Plan approved. You may now delegate tasks to sub-agents."
        elif ctx.ask:
            summary = "\n".join(f"  [{t['status']}] {t['id']}: {t['description']}"
                               for t in tasks)
            approval = ctx.ask(
                f"The following plan has been created. Approve and start execution?\n{summary}"
            )
            if approval and any(kw in approval.lower() for kw in ["no", "not", "dis", "reject", "revise"]):
                return "Plan not approved. Please revise the plan."
            plan.approve()
            return "Plan approved and ready for execution."
        return result

    if action == "approve":
        plan.approve()
        return "Plan approved."

    if action == "update":
        return plan.update(tasks)

    if action == "note":
        return plan.note(args.get("task_id", ""), args.get("notes", ""))

    return f"plan: unknown action {action!r}, expected create/update/approve/note"


register_tool(PLAN_SCHEMA, _handle_plan)