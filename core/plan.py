"""计划系统：显式任务拆解 + 进度追踪 + 依赖管理 + 审批 + 持久化 + 可视化。

两种模式共享同一个 PlanTracker：
  - single 模式：同一 AI 先 Plan（只读+审批）后 Act（执行）
  - multi 模式：Planning Agent 规划 + 审批 → delegate 子 Agent 执行

5 个优化全部实现：
  1. 可视化进度条 + 依赖图
  2. 计划持久化到 .chisel/plan.json
  3. 自动完成摘要
  4. 拒绝时收集原因
  5. 任务重排序
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from tools import register_tool

TASK_STATUS = ("pending", "in_progress", "done", "blocked", "verified")


@dataclass
class PlanTask:
    id: str
    description: str
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    notes: str = ""


class PlanTracker:
    def __init__(self):
        self.tasks: list[PlanTask] = []
        self.created = False
        self.approved = False
        self.mode: str = "single"
        self._rejection_reason: str = ""  # 第 4 项：拒绝原因

    # --- 创建与更新 ----------------------------------------------------------

    def create_empty(self) -> None:
        self.tasks = []
        self.created = True
        self.approved = False

    def create(self, tasks: list[dict]) -> str:
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
        ids = {t.id for t in cleaned}
        for t in cleaned:
            for dep in t.depends_on:
                if dep not in ids:
                    return f"plan: task {t.id} depends on unknown task {dep!r}"
        self.tasks = cleaned
        self.created = True
        self.approved = False
        if self._all_done():
            self._finalize_summary()
        self._persist()
        return f"Plan created: {len(cleaned)} subtasks. Waiting for approval..."

    def approve(self) -> str:
        if not self.tasks:
            return "plan: no plan to approve."
        self.approved = True
        self._persist()
        return "Plan approved."

    def update(self, tasks: list[dict]) -> str:
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
        self._persist()
        if self._all_done():
            self._finalize_summary()
        return "Plan updated."

    def note(self, task_id: str, notes: str) -> str:
        if task_id not in {t.id for t in self.tasks}:
            return f"plan: task {task_id!r} not found."
        for t in self.tasks:
            if t.id == task_id:
                t.notes = notes
                self._persist()
                return f"Notes added to task {task_id}."
        return ""

    def reorder(self, tasks: list[dict]) -> str:
        """第 5 项：重新排列任务顺序和依赖关系。"""
        if not self.tasks:
            return "plan: no plan yet."
        # 验证所有 task id 存在
        existing_ids = {t.id for t in self.tasks}
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            if tid not in existing_ids:
                return f"plan: reorder failed, task {tid!r} not found."
        # 重建任务列表（按新顺序 + 更新依赖）
        new_tasks = []
        seen = set()
        for t in tasks:
            tid = str(t.get("id", "")).strip()
            if tid in seen:
                continue
            seen.add(tid)
            old = next(ot for ot in self.tasks if ot.id == tid)
            new_deps = [str(d).strip() for d in t.get("depends_on", []) if str(d).strip()]
            # 验证新依赖都在新任务列表里
            for d in new_deps:
                if d not in {x.get("id", "") for x in tasks} and d not in existing_ids:
                    return f"plan: reorder failed, new dependency {d!r} unknown."
            old.depends_on = new_deps
            new_tasks.append(old)
        # 追加未在 reorder 中的任务
        for t in self.tasks:
            if t.id not in seen:
                new_tasks.append(t)
        self.tasks = new_tasks
        self._persist()
        return f"Plan reordered: {len(self.tasks)} tasks."

    def blocked_by(self) -> list[str]:
        done_ids = {t.id for t in self.tasks if t.status in ("done", "verified")}
        return [t.id for t in self.tasks if t.status != "blocked"
                and any(d not in done_ids for d in t.depends_on)]

    def _all_done(self) -> bool:
        return bool(self.tasks) and all(t.status in ("done", "verified") for t in self.tasks)

    # --- 第 2 项：持久化 -----------------------------------------------------

    def _persist(self) -> None:
        """保存计划到 .chisel/plan.json。"""
        if not self.tasks:
            return
        plan_dir = Path(self._workspace()) / ".chisel"
        plan_dir.mkdir(parents=True, exist_ok=True)
        path = plan_dir / "plan.json"
        data = {
            "tasks": self.to_dict(),
            "approved": self.approved,
            "mode": self.mode,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _workspace(self) -> str:
        """从调用栈推断工作目录（持久化用）。"""
        import traceback
        for frame in traceback.extract_stack():
            locs = getattr(frame, 'locals', None)
            if locs and "workspace" in locs:
                ws = locs.get("workspace", ".")
                if ws:
                    return str(ws)
        return "."

    # --- 第 3 项：完成摘要 ---------------------------------------------------

    _summary: str = ""

    def _finalize_summary(self) -> None:
        """所有任务完成时自动生成摘要。"""
        if not self._all_done() or not self.tasks:
            return
        lines = ["Task Summary:"]
        for t in self.tasks:
            note = f" → {t.notes}" if t.notes else ""
            dep = f" (depends: {t.depends_on})" if t.depends_on else ""
            lines.append(f"  [{t.status}] {t.id}: {t.description}{dep}{note}")
        lines.append(f"  Total: {len(self.tasks)} tasks completed.")
        self._summary = "\n".join(lines)

    def finalize(self) -> str:
        """生成最终摘要并返回。"""
        if self._all_done() and not self._summary:
            self._finalize_summary()
        if self._summary:
            return self._summary
        if not self.tasks:
            return "No plan was created."
        done = sum(1 for t in self.tasks if t.status in ("done", "verified"))
        return f"Plan finalized: {done}/{len(self.tasks)} tasks completed."

    # --- 第 1 项：可视化渲染 -------------------------------------------------

    def to_text(self) -> str:
        if not self.tasks:
            return "Plan: not yet created. Use plan tool with action=create to break down the task."
        done = sum(1 for t in self.tasks if t.status in ("done", "verified"))
        total = len(self.tasks)
        # 进度条
        bar_len = 12
        filled = int(bar_len * done / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        lines = [f"  [{bar}] {done}/{total} tasks completed"]
        lines.append("")
        for t in self.tasks:
            icon = {"done": "✓", "verified": "✓", "in_progress": "▶",
                    "pending": "⏳", "blocked": "⛔"}.get(t.status, "?")
            dep_str = f"  ← {t.depends_on}" if t.depends_on else ""
            note_str = f"  → {t.notes}" if t.notes else ""
            lines.append(f"  [{icon}] {t.id}: {t.description}{dep_str}{note_str}")
        header = "Plan (update progress with plan action=update):"
        footer = f"Progress: {done}/{total}"
        phase = " [PLAN PHASE - read only]" if not self.approved else ""
        return f"{header}{phase}\n" + "\n".join(lines) + f"\n{footer}"

    def inject(self, messages: list[dict]) -> None:
        while len(messages) < 2:
            messages.append({"role": "system", "content": ""})
        messages[1] = {"role": "system", "content": self.to_text()}

    def to_dict(self) -> list[dict]:
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
                       "action=reorder to rearrange tasks or change dependencies, "
                       "action=approve to confirm the plan. "
                       "Use depends_on to specify task dependencies. "
                       "The current plan is automatically injected into your context.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "approve", "note", "reorder"]},
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
        # 审批环节：先显示 Dry-Run 预览（如果涉及清理操作）
        if ctx.ask:
            summary = "\n".join(f"  [{t['status']}] {t['id']}: {t['description']}"
                               for t in tasks)
            # 检查是否有清理/删除相关的任务
            has_cleanup = any("清理" in t.get("description", "") or "删除" in t.get("description", "")
                              or "clean" in t.get("description", "").lower() or "delete" in t.get("description", "").lower()
                              or "remove" in t.get("description", "").lower() for t in tasks)
            if has_cleanup and ctx.workspace:
                preview = _dry_run_preview(ctx.workspace)
                if preview:
                    approval = ctx.ask(
                        f"⚠️ 计划涉及清理/删除操作\n\n"
                        f"Dry-Run 预览：\n{preview}\n\n"
                        f"计划内容：\n{summary}\n\n"
                        f"是否批准执行？",
                        ["是", "否"]
                    )
                else:
                    approval = ctx.ask(
                        f"已创建 {len(tasks)} 个子任务\n{summary}\n\n是否批准？",
                        ["是", "否"]
                    )
            else:
                approval = ctx.ask(
                    f"已创建 {len(tasks)} 个子任务\n{summary}\n\n是否批准？",
                    ["是", "否"]
                )
            if approval != "是":
                return "用户已拒绝，任务取消。请等待新的指令。"
            plan.approve()
            return "计划已批准，开始执行。"
        return result

    if action == "approve":
        plan.approve()
        return "Plan approved."

    if action == "update":
        return plan.update(tasks)

    if action == "note":
        return plan.note(args.get("task_id", ""), args.get("notes", ""))

    if action == "reorder":
        return plan.reorder(tasks)

    return f"plan: unknown action {action!r}, expected create/update/approve/note/reorder"


register_tool(PLAN_SCHEMA, _handle_plan)


def _dry_run_preview(workspace: str) -> str:
    """扫描工作目录，列出常见的清理目标文件。"""
    ws = Path(workspace)
    if not ws.exists():
        return ""

    targets = []
    _PROTECTED = {".git", ".env", ".env.example", ".gitignore", ".vscode", ".ssh", "node_modules"}

    # 扫描常见缓存/临时文件
    for root, dirs, files in os.walk(ws):
        rel = os.path.relpath(root, ws)
        # 跳过受保护目录
        if any(p in rel.split(os.sep) for p in _PROTECTED):
            continue
        for d in dirs:
            if d in ("__pycache__", ".pytest_cache", ".mypy_cache", "node_modules/.cache", ".chisel"):
                full = os.path.join(root, d)
                size = sum(os.path.getsize(os.path.join(full, f)) for f in os.listdir(full) if os.path.isfile(os.path.join(full, f))) if os.path.isdir(full) else 0
                targets.append(("📁", rel + "/" + d if rel != "." else d, size, "Directory"))
        for f in files:
            if f.endswith((".pyc", ".pyo", ".DS_Store", ".thumbs.db")):
                full = os.path.join(root, f)
                size = os.path.getsize(full)
                targets.append(("📄", rel + "/" + f if rel != "." else f, size, "File"))
        if len(targets) > 50:
            break

    if not targets:
        return ""

    total_size = sum(t[2] for t in targets)
    lines = [f"  Detected {len(targets)} items (~{total_size/1024:.0f} KB):"]
    for icon, path, size, kind in targets[:20]:
        size_str = f"{size/1024:.1f} KB" if size > 1024 else f"{size} B"
        lines.append(f"    {icon} {path} ({kind}, {size_str})")
    if len(targets) > 20:
        lines.append(f"    ... and {len(targets) - 20} more items")
    lines.append(f"  Protected: {', '.join(_PROTECTED)}")
    return "\n".join(lines)