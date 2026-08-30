"""core/plan 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.plan import PlanTracker


def test_create_and_update():
    p = PlanTracker()
    r = p.create([
        {"id": "1", "description": "analyze dependencies", "status": "in_progress"},
        {"id": "2", "description": "write core logic", "status": "pending"},
    ])
    assert "Plan created" in r
    r = p.update([{"id": "1", "status": "done"}])
    assert "Plan updated" in r
    text = p.to_text()
    assert "[in_progress]" not in text
    assert "[done] 1" in text


def test_inject_writes_placeholder():
    p = PlanTracker()
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}]
    p.inject(msgs)
    assert msgs[1]["role"] == "system"
    assert "Plan" in msgs[1]["content"]


def test_inject_rewrites_in_place():
    p = PlanTracker()
    p.create([{"id": "a", "description": "task A", "status": "pending"}])
    # Override: auto-approve since ask_user is not available in tests
    p.approve()
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "system", "content": "old plan"},
        {"role": "user", "content": "t"},
        {"role": "assistant", "content": "", "tool_calls": []},
    ]
    p.inject(msgs)
    # 计划占位被原位改写，不动其他消息
    assert msgs[0]["content"] == "s"
    assert msgs[1]["content"].startswith("Plan")
    assert msgs[2]["content"] == "t"
    assert msgs[3]["role"] == "assistant"


def test_update_invalid_status():
    p = PlanTracker()
    p.create([{"id": "a", "description": "task A", "status": "pending"}])
    r = p.update([{"id": "a", "status": "weird"}])
    assert "must be one of" in r


def test_update_unknown_id():
    p = PlanTracker()
    p.create([{"id": "a", "description": "task A", "status": "pending"}])
    r = p.update([{"id": "zz", "status": "done"}])
    assert "not found" in r


def test_update_before_create():
    p = PlanTracker()
    r = p.update([{"id": "a", "status": "done"}])
    assert "no plan yet" in r


def test_depends_on():
    p = PlanTracker()
    r = p.create([
        {"id": "1", "description": "setup", "status": "done"},
        {"id": "2", "description": "build", "status": "pending", "depends_on": ["1"]},
        {"id": "3", "description": "test", "status": "pending", "depends_on": ["2"]},
    ])
    assert "Plan created" in r
    # 任务 1 已 done，任务 2 不阻塞
    blocked = p.blocked_by()
    assert "2" not in blocked
    assert "3" in blocked  # 任务 2 未完成，3 阻塞


def test_depends_on_unknown():
    p = PlanTracker()
    r = p.create([
        {"id": "1", "description": "task A", "status": "pending", "depends_on": ["nonexistent"]},
    ])
    assert "unknown task" in r


def test_approve_flow():
    p = PlanTracker()
    p.create([{"id": "1", "description": "task A", "status": "pending"}])
    assert p.approved is False
    p.approve()
    assert p.approved is True


def test_note():
    p = PlanTracker()
    p.create([{"id": "1", "description": "task A", "status": "pending"}])
    r = p.note("1", "Found bug in function X")
    assert "Notes added" in r
    assert p.tasks[0].notes == "Found bug in function X"