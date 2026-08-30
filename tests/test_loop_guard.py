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
    g.note_call("bash", {"command": "python t.py"})  # 3rd
    assert len(g.drain_warnings()) == 1
    g.note_call("bash", {"command": "python t.py"})  # 4th
    g.note_call("bash", {"command": "python t.py"})  # 5th
    assert g.should_abort() is True


def test_loop_guard_resets_on_interleaved_tool():
    g = LoopGuard(soft=3, hard=5)
    g.note_call("bash", {"command": "python test.py"})
    g.note_call("bash", {"command": "python test.py"})
    g.note_call("edit_file", {"path": "a.py", "original_lines": "x", "updated_lines": "y"})
    g.note_call("bash", {"command": "python test.py"})
    assert g.should_abort() is False
    assert g.drain_warnings() == []


def test_loop_guard_meta_tools_not_counted():
    g = LoopGuard(soft=1, hard=2)
    g.note_call("plan", {"action": "update", "tasks": []})
    g.note_call("plan", {"action": "update", "tasks": []})
    assert g.should_abort() is False


def test_mistake_tracker_consecutive_errors():
    m = MistakeTracker(soft=2, hard=3)
    m.track("Traceback (most recent call last):", "bash")
    m.track("Tool execution error: FileNotFoundError: x", "bash")
    assert len(m.drain_warnings()) == 1
    m.track("AssertionError: expected 0, got 1", "bash")
    assert m.should_abort() is True


def test_mistake_tracker_resets_on_success():
    m = MistakeTracker(soft=2, hard=3)
    m.track("Error: something went wrong", "bash")
    m.track("Written a.py (42 chars)", "bash")  # success, reset
    m.track("Error: something went wrong again", "bash")
    assert m.should_abort() is False


def test_mistake_tracker_ignores_normal_output():
    m = MistakeTracker(soft=1, hard=2)
    m.track("exit code 0\nhello world", "bash")
    assert m.should_abort() is False


def test_mistake_tracker_classification():
    """Cline 风格：3 种错误类型分别追踪。"""
    m = MistakeTracker(soft=3, hard=5)
    m.track("APIConnectionError: timeout", "bash")  # api_error
    m.track("RateLimit: too many requests", "bash")  # api_error
    m.track("Invalid JSON arguments.", "bash")  # invalid_tool_call
    assert m.should_abort() is False  # 3 total, soft=3 → warning
    assert len(m.drain_warnings()) == 1
    m.track("Tool execution error: something broke", "edit_file")  # tool_execution_failed
    m.track("Access denied: path escapes", "edit_file")  # tool_execution_failed
    assert m.should_abort() is True  # 5 total, hard=5 → abort


def test_mistake_tracker_different_types_accumulate():
    """不同类型错误累计计数。"""
    m = MistakeTracker(soft=3, hard=5)
    m.track("APIConnectionError: network fail", "bash")  # api_error
    m.track("Invalid JSON arguments.", "bash")  # invalid_tool_call
    m.track("Tool execution error: crash", "bash")  # tool_execution_failed
    assert m.should_abort() is False  # 3 total, soft=3 → warning
    assert len(m.drain_warnings()) == 1