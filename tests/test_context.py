"""core/context 的离线单测（无 API key）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import context


class FakeClient:
    """估计函数：每条消息按 content 长度估算（约 1 token/字），可自定。"""

    def __init__(self, summary="[压缩摘要] 已压缩该回合要点"):
        self.summary = summary
        self.summarize_calls = 0

    def estimate_messages_tokens(self, messages):
        return sum(len(str(m.get("content") or "")) for m in messages) // 3 + 1

    def chat(self, messages, tools=None):
        self.summarize_calls += 1
        class _Msg:
            content = self.summary
        class _Choice:
            message = _Msg()
        class _Resp:
            choices = [_Choice()]
        return _Resp()


def make_round_trip(calls=2, content="x" * 100):
    """构造一个完整工具回合：1 条 assistant(tool_calls) + calls 条 tool 消息。"""
    msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": f"call{i}"} for i in range(calls)]}]
    for i in range(calls):
        msgs.append({"role": "tool", "tool_call_id": f"call{i}", "content": content})
    return msgs


def base_messages():
    """pinned 三条 + 两个完整回合（小回合，可被折叠）。"""
    msgs = [
        {"role": "system", "content": "system"},
        {"role": "system", "content": "计划占位"},
        {"role": "user", "content": "用户任务"},
    ]
    msgs += make_round_trip(calls=2, content="small" * 10)   # 回合1
    msgs += make_round_trip(calls=2, content="small" * 10)   # 回合2
    return msgs


def assert_no_orphan_tools(messages):
    """不变式：每条 tool 消息必须紧跟其 assistant 生成者；assistant(tool_calls) 的 tool 结果必须齐全。"""
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            assert i + n < len(messages), f"第 {i} 条 assistant 的 tool 结果不完整"
            for j in range(1, n + 1):
                assert messages[i + j].get("role") == "tool", f"第 {i+j} 条应为 tool"
            i += n + 1
        else:
            assert m.get("role") != "tool" or i > 0, "孤立 tool 消息"
            i += 1


def test_truncate_output_short():
    assert context.truncate_output("abc") == "abc"


def test_truncate_output_long():
    text = "A" * 5000
    out = context.truncate_output(text, head=100, tail=50)
    assert len(out) < 300
    assert "omitted" in out or "truncated" in out
    assert out.startswith("A" * 100)
    assert out.endswith("A" * 50)


def test_truncate_output_saves_to_file(tmp_path):
    """OpenHands 风格：截断时保存完整内容到文件。"""
    text = "X" * 5000
    out = context.truncate_output(text, head=100, tail=50, save_dir=str(tmp_path), tool_prefix="bash")
    assert "truncated" in out.lower()
    # 检查文件是否保存在 .chisel/truncated/ 下
    saved = list((tmp_path / ".chisel" / "truncated").glob("*"))
    assert len(saved) >= 1
    assert saved[0].read_text(encoding="utf-8") == text


def test_compress_noop_when_under_limit():
    msgs = base_messages()
    client = FakeClient()
    before = list(msgs)
    changed = context.compress_context(msgs, client, max_tokens=100000)
    assert changed is False
    assert msgs == before


def test_compress_folds_old_small_rounds():
    msgs = base_messages()
    client = FakeClient()
    # 总 token 估算 ≈ 72，设 50 → 必须删掉最老的回合
    changed = context.compress_context(msgs, client, max_tokens=50)
    assert changed is True
    assert_no_orphan_tools(msgs)
    # pinned 三条永不删
    assert msgs[0]["content"] == "system"
    assert msgs[1]["content"] == "计划占位"
    assert msgs[2]["content"] == "用户任务"
    # 最老的回合（index>=3 的第一个 assistant）应已被折叠
    assert len(msgs) < len(base_messages())
    # 配对不变式：tool 消息总数 == 所有 assistant 的 tool_calls 总数
    total_calls = sum(len(m["tool_calls"]) for m in msgs if m.get("tool_calls"))
    assert sum(1 for m in msgs if m["role"] == "tool") == total_calls


def test_compress_summarizes_big_round():
    """大回合（内容超 FOLD_MAX_CHARS）不被整段删，而走 LLM 摘要。"""
    msgs = base_messages()[:-6]  # 只留 pinned + 一个回合
    big = make_round_trip(calls=1, content="E" * 12000)  # 大回合
    msgs += big
    client = FakeClient()
    changed = context.compress_context(msgs, client, max_tokens=100)
    assert changed is True
    assert_no_orphan_tools(msgs)
    assert client.summarize_calls >= 1
    # 应出现摘要消息
    assert any(m.get("role") == "user" and "Summary" in str(m.get("content")) for m in msgs)


def test_compress_never_touches_pinned():
    msgs = base_messages()
    client = FakeClient()
    context.compress_context(msgs, client, max_tokens=0)  # 极限压缩
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "system"
    assert msgs[2]["role"] == "user"
