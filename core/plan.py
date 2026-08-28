"""计划追踪：显式任务拆解 + 进度更新 + 每轮注入上下文。

设计来源（借鉴 OpenHands PlannerAgent / PlanAction 的思路，自写）：
  计划 = 一份带状态的任务清单，存在会话状态里，每回合序列化成文字写入
  system 上下文（messages[1] 占位），让模型自己维护进度。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools import register_tool

TASK_STATUS = ("pending", "in_progress", "done")


@dataclass
class PlanTask:
    id: str
    description: str
    status: str = "pending"  # pending / in_progress / done


class PlanTracker:
    def __init__(self):
        self.tasks: list[PlanTask] = []
        self.created = False

    def create_empty(self) -> None:
        self.tasks = []
        self.created = True

    def create(self, tasks: list[dict]) -> str:
        """创建/整体替换计划。tasks: [{id, description, status}]"""
        cleaned: list[PlanTask] = []
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            desc = str(t.get("description", "")).strip()
            status = t.get("status", "pending")
            if status not in TASK_STATUS:
                return f"plan 失败：status 必须是 {TASK_STATUS} 之一，收到 {status!r}"
            if not tid or not desc:
                return "plan 失败：每个任务都需要非空的 id 和 description"
            cleaned.append(PlanTask(id=tid, description=desc, status=status))
        self.tasks = cleaned
        self.created = True
        return f"计划已创建：共 {len(cleaned)} 个子任务。"

    def update(self, tasks: list[dict]) -> str:
        """更新进度（可只更新部分任务的状态）。"""
        if not self.tasks:
            return "plan 失败：还没有计划，请先 action=create 拆解任务。"
        by_id = {t.id: t for t in self.tasks}
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            if tid not in by_id:
                return (
                    f"plan 失败：任务 id {tid!r} 不存在。"
                    f"当前计划任务：{', '.join(t.id for t in self.tasks)}"
                )
            status = t.get("status")
            if status not in TASK_STATUS:
                return f"plan 失败：status 必须是 {TASK_STATUS} 之一"
            by_id[tid].status = status
        return "计划进度已更新。"

    def to_text(self) -> str:
        if not self.tasks:
            return "当前计划：尚未创建。请先用 plan 工具（action=create）把任务拆成子任务清单。"
        done = sum(1 for t in self.tasks if t.status == "done")
        lines = [f"- [{t.status:>4}] {t.id}: {t.description}" for t in self.tasks]
        return (
            "当前计划（每完成一步请用 plan 工具的 action=update 更新状态）：\n"
            + "\n".join(lines)
            + f"\n完成进度：{done}/{len(self.tasks)}"
        )

    def inject(self, messages: list[dict]) -> None:
        """把当前计划原位写回 messages[1]（约定为计划占位的 system 消息）。"""
        while len(messages) < 2:
            messages.append({"role": "system", "content": ""})
        messages[1] = {"role": "system", "content": self.to_text()}

    def finalize(self) -> None:
        pass  # 预留：可把最终计划落盘到 .chisel/


# --- plan 工具（OpenAI function calling schema + 处理器） -------------------

PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan",
        "description": "维护子任务清单。任务开始时先调用一次 action=create 把任务拆成子任务；"
                       "每完成一步用 action=update 更新状态。当前计划会自动注入你的上下文，无需手动读取。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update"]},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                        },
                        "required": ["id", "description", "status"],
                    },
                    "description": "create 时给出全部子任务；update 时给出要更新的任务（可只给部分）",
                },
            },
            "required": ["action", "tasks"],
        },
    },
}


def _handle_plan(ctx, args: dict) -> str:
    plan = ctx.plan
    action = args.get("action")
    tasks = args.get("tasks") or []
    if action == "create":
        return plan.create(tasks)
    if action == "update":
        return plan.update(tasks)
    return f"plan 失败：未知 action {action!r}，应为 create 或 update"


register_tool(PLAN_SCHEMA, _handle_plan)
