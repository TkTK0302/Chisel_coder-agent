"""core/completion 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeCtx:
    pass


def test_completion_handler_sets_result():
    from core.completion import _handle_completion

    ctx = FakeCtx()
    r = _handle_completion(ctx, {"result": "完成了修复任务。"})
    assert r == "完成了修复任务。"
    assert ctx._completion_result == "完成了修复任务。"


def test_completion_handler_default_result():
    from core.completion import _handle_completion

    ctx = FakeCtx()
    r = _handle_completion(ctx, {})
    assert r == "任务完成。"
    assert ctx._completion_result == "任务完成。"


def test_completion_registered():
    # 先引入 core.completion 触发注册
    import core.completion  # noqa: F401
    from tools import available_tool_names
    assert "attempt_completion" in available_tool_names()