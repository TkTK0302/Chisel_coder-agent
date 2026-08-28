"""core/loop_guard 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.loop_guard import LoopGuard, MistakeTracker


def test_loop_guard_counts_consecutive_identical():
    g = LoopGuard(soft=3, hard=5)
    g.note_call("bash", {"command": "python t.py"})
    g.note_call("bash", {"command": "python t.py"})
    assert g.should_abort() is False
    assert g.drain_warnings() == []
    g.note_call("bash", {"command": "python t.py"})  # 连续第 3 次
    assert len(g.drain_warnings()) == 1
    g.note_call("bash", {"command": "python t.py"})  # 第 4 次
    g.note_call("bash", {"command": "python t.py"})  # 第 5 次
    assert g.should_abort() is True


def test_loop_guard_resets_on_interleaved_tool():
    """中间穿插其他工具即重置计数（leap 修复的 bash→edit→bash 不被误杀）。"""
    g = LoopGuard(soft=3, hard=5)
    g.note_call("bash", {"command": "python test.py"})   # 1
    g.note_call("bash", {"command": "python test.py"})   # 2
    g.note_call("edit_file", {"path": "a.py", "original_lines": "x", "updated_lines": "y"})  # 穿插
    g.note_call("bash", {"command": "python test.py"})   # 重置后 1
    assert g.should_abort() is False
    assert g.drain_warnings() == []


def test_loop_guard_meta_tools_not_counted():
    g = LoopGuard(soft=1, hard=2)  # 极低阈值
    g.note_call("plan", {"action": "update", "tasks": []})
    g.note_call("plan", {"action": "update", "tasks": []})
    assert g.should_abort() is False


def test_mistake_tracker_consecutive_errors():
    m = MistakeTracker(soft=2, hard=3)
    m.track("Traceback (most recent call last):")
    m.track("工具执行出错: FileNotFoundError: x")
    assert len(m.drain_warnings()) == 1
    m.track("AssertionError: expected 0, got 1")
    assert m.should_abort() is True


def test_mistake_tracker_resets_on_success():
    m = MistakeTracker(soft=2, hard=3)
    m.track("错误: xxx")
    m.track("已写入 a.py")  # 成功，重置
    m.track("错误: xxx")
    assert m.should_abort() is False


def test_mistake_tracker_ignores_normal_output():
    m = MistakeTracker(soft=1, hard=2)
    m.track("exit code 0\nhello world")
    assert m.should_abort() is False
