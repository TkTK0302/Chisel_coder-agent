"""core/plan 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.plan import PlanTracker


def test_create_and_update():
    p = PlanTracker()
    r = p.create([
        {"id": "1", "description": "分析依赖", "status": "in_progress"},
        {"id": "2", "description": "写核心逻辑", "status": "pending"},
    ])
    assert "2" in r
    r = p.update([{"id": "1", "status": "done"}])
    assert "已更新" in r
    text = p.to_text()
    assert "[in_progress]" not in text
    assert "[done] 1" in text


def test_inject_writes_placeholder():
    p = PlanTracker()
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}]
    p.inject(msgs)
    assert msgs[1]["role"] == "system"
    assert "当前计划" in msgs[1]["content"]


def test_inject_rewrites_in_place():
    p = PlanTracker()
    p.create([{"id": "a", "description": "任务A", "status": "pending"}])
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "system", "content": "旧计划"},
        {"role": "user", "content": "t"},
        {"role": "assistant", "content": "", "tool_calls": []},
    ]
    p.inject(msgs)
    # 计划占位被原位改写，不动其他消息
    assert msgs[0]["content"] == "s"
    assert msgs[1]["content"].startswith("当前计划")
    assert msgs[2]["content"] == "t"
    assert msgs[3]["role"] == "assistant"


def test_update_invalid_status():
    p = PlanTracker()
    p.create([{"id": "a", "description": "任务A", "status": "pending"}])
    r = p.update([{"id": "a", "status": "weird"}])
    assert "失败" in r


def test_update_unknown_id():
    p = PlanTracker()
    p.create([{"id": "a", "description": "任务A", "status": "pending"}])
    r = p.update([{"id": "zz", "status": "done"}])
    assert "不存在" in r


def test_update_before_create():
    p = PlanTracker()
    r = p.update([{"id": "a", "status": "done"}])
    assert "还没有计划" in r
