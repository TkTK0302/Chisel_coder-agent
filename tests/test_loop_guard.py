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
    """Q6: 成功加权清零 —— 一次成功减半，两次成功归零。"""
    m = MistakeTracker(soft=2, hard=3)
    m.track("Traceback: something went wrong", "bash", exit_code=1)
    m.track("Traceback: another error", "bash", exit_code=1)
    assert m._errors["tool_execution_failed"] == 2
    m.track("Written a.py (42 chars)", "bash")  # 一次成功 → 减半
    assert m._errors["tool_execution_failed"] == 1  # 2 // 2 = 1
    m.track("Written b.py (42 chars)", "bash")  # 两次成功 → 归零
    assert m._errors["tool_execution_failed"] == 0
    m.track("Traceback: something went wrong again", "bash", exit_code=1)
    assert m.should_abort() is False  # 1 次，还没到 hard=3


def test_mistake_tracker_ignores_normal_output():
    """Q3: exit_code=0 的正常输出不被判定为错误。"""
    m = MistakeTracker(soft=1, hard=2)
    m.track("exit code 0\nhello world", "bash", exit_code=0)
    assert m.should_abort() is False
    # exit_code=0 但包含 AssertionError 仍判定为错误
    m.track("AssertionError: test failed", "bash", exit_code=0)
    assert m._errors["tool_execution_failed"] == 1


def test_mistake_tracker_classification():
    """Q4: 三类错误分开计数，只有 tool_execution_failed 触发 abort。"""
    m = MistakeTracker(soft=3, hard=5)
    m.track("APIConnectionError: timeout", "bash")  # api_error
    m.track("RateLimit: too many requests", "bash")  # api_error
    m.track("Invalid JSON arguments.", "bash")  # invalid_tool_call
    # Q4: api_error + invalid_tool_call 不参与 abort 判断
    assert m.should_abort() is False
    assert m._errors["tool_execution_failed"] == 0
    m.track("Tool execution error: something broke", "edit_file")  # tool_execution_failed
    m.track("Access denied: path escapes", "edit_file")  # tool_execution_failed
    assert m._errors["tool_execution_failed"] == 2  # 只有 2 次，未到 hard=5
    assert m.should_abort() is False


def test_mistake_tracker_different_types_accumulate():
    """Q4: 不同类型错误分开追踪，api_error 和 invalid_tool_call 不触发 abort。"""
    m = MistakeTracker(soft=3, hard=5)
    m.track("APIConnectionError: network fail", "bash")  # api_error
    m.track("Invalid JSON arguments.", "bash")  # invalid_tool_call
    m.track("Tool execution error: crash", "bash")  # tool_execution_failed
    # Q4: 只有 tool_execution_failed=1，未到 soft=3
    assert m._errors["tool_execution_failed"] == 1
    assert m.should_abort() is False


def test_mistake_tracker_abort_only_on_tool_failure():
    """Q4: api_error 即使达到 hard 阈值也不触发 abort。"""
    m = MistakeTracker(soft=3, hard=5)
    for _ in range(7):
        m.track("APIConnectionError: timeout", "bash")
    assert m._errors["api_error"] == 7
    assert m.should_abort() is False  # api_error 不触发 abort
    # 但 tool_execution_failed 达到 hard 会触发
    for _ in range(5):
        m.track("Tool execution error: crash", "bash")
    assert m.should_abort() is True


def test_mistake_tracker_weighted_reset():
    """Q6: 成功加权清零验证。"""
    m = MistakeTracker(soft=3, hard=5)
    # 连续 4 次错误
    for _ in range(4):
        m.track("Tool execution error: x", "bash")
    assert m._errors["tool_execution_failed"] == 4
    # 1 次成功 → 减半
    m.track("Written a.py (42 chars)", "bash")
    assert m._errors["tool_execution_failed"] == 2
    # 再 1 次成功 → 归零
    m.track("Written b.py (42 chars)", "bash")
    assert m._errors["tool_execution_failed"] == 0