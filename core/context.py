"""上下文管理：长输出截断 + 超限压缩。

设计来源（借鉴思路，自写）：
  - 长命令输出的"头尾截断 + 完整内容保存到文件"：OpenHands maybe_truncate。
  - 超限时的"确定性折叠"：Cline basic-compaction。
  - 仍超限时的 LLM 摘要：Aider ChatSummary。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def truncate_output(
    text: str,
    head: int = 3000,
    tail: int = 1500,
    save_dir: str | None = None,
    tool_prefix: str = "output",
) -> str:
    """长输出头尾截断，完整内容保存到文件（OpenHands 风格）。

    如果 save_dir 指定了目录，截断时把完整内容保存到
    .chisel/truncated/ 下，并在省略标记中告诉 AI 文件路径。
    AI 可以后续用 read_file 查看完整内容。
    """
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail

    # 保存完整内容到文件
    file_path = None
    if save_dir:
        trunc_dir = Path(save_dir) / ".chisel" / "truncated"
        trunc_dir.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        filename = f"{tool_prefix}_output_{content_hash}.txt"
        save_path = trunc_dir / filename
        if not save_path.exists():
            save_path.write_text(text, encoding="utf-8")
        file_path = save_path

    notice = (
        f"\n[Output truncated. Full content saved to {file_path}]\n"
        if file_path
        else f"\n[... {omitted} chars omitted ...]\n"
    )
    return text[:head] + notice + text[-tail:]


def _round_trip_span(messages: list[dict], i: int) -> tuple[int, int] | None:
    """若 messages[i] 是带 tool_calls 的 assistant 且其后跟齐了 tool 结果，
    返回 [i, end]（含 end 的完整工具回合区间），否则 None。"""
    if messages[i].get("role") != "assistant":
        return None
    calls = messages[i].get("tool_calls")
    if not calls:
        return None
    n = len(calls)
    if i + n >= len(messages):  # 回合不完整（异常态），整组不可删
        return None
    for j in range(1, n + 1):
        if messages[i + j].get("role") != "tool":
            return None
    return (i, i + n)


def _oldest_round_trip(messages: list[dict], pinned: int) -> tuple[int, int] | None:
    """从 pinned 起找第一个完整工具回合的 [start, end]。"""
    for i in range(pinned, len(messages)):
        span = _round_trip_span(messages, i)
        if span:
            return span
    return None


def _summarize_turn(msgs: list[dict], client, max_chars: int = 600) -> str:
    """把一组工具执行记录用模型压缩成一条摘要。失败返回空串（调用方保留原文）。"""
    text = "\n".join(
        f"[{m.get('role')}] {(m.get('content') or '')[:1500]}" for m in msgs
    )
    try:
        resp = client.chat(
            [
                {
                    "role": "system",
                    "content": "你是上下文压缩器。把下面这段编程智能体的工具执行记录压缩成要点摘要："
                               "保留关键决定、报错与结果，删除重复输出。用中文，不超过 600 字。",
                },
                {"role": "user", "content": text},
            ]
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# 完整回合内容总字符数超过该值时，不直接删除而是交给 LLM 摘要（保留要点）。
# 小回合直接删（省 token），大回合（如大段报错/日志）摘要后保留一条精简记忆。
FOLD_MAX_CHARS = 8000


def _span_size(messages: list[dict], span: tuple[int, int]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages[span[0] : span[1] + 1])


def compress_context(
    messages: list[dict],
    client,
    max_tokens: int,
    pinned: int = 3,
    max_summary_chars: int = 600,
) -> bool:
    """把 messages 原地压缩到 max_tokens 以内。返回是否发生了压缩。

    策略（参考 Cline basic-compaction / Aider ChatSummary 的思路，自写）：
      1. 确定性折叠：整组删除最老的"小"完整工具回合（内容量 <= FOLD_MAX_CHARS），
         删到不超限或无可删。删除比摘要便宜，小回合直接删。
      2. LLM 摘要：仍超限时，把最老的"大"完整回合用模型压缩成一条摘要替换，
         保留要点而不是整段丢弃。

    不变式：
      - messages[:pinned]（system / 计划占位 / 用户任务）永不删。
      - 一条 assistant(tool_calls) 与其 tool 结果同存同删，绝不留孤立 tool 消息。
    """
    if client.estimate_messages_tokens(messages) <= max_tokens:
        return False
    changed = False

    # 1) 确定性折叠：删最老的"小"回合，删到不超限或无可删
    while client.estimate_messages_tokens(messages) > max_tokens:
        span = _oldest_round_trip(messages, pinned)
        if span is None:
            break
        if _span_size(messages, span) > FOLD_MAX_CHARS:
            break  # 大回合留给摘要，别整段丢
        del messages[span[0] : span[1] + 1]
        changed = True
    if client.estimate_messages_tokens(messages) <= max_tokens:
        return changed

    # 2) LLM 摘要最老的一个完整回合（大回合：删掉会丢关键报错/结论，摘要保留）
    span = _oldest_round_trip(messages, pinned)
    if span is not None:
        summary = _summarize_turn(messages[span[0] : span[1] + 1], client, max_summary_chars)
        if summary:
            del messages[span[0] : span[1] + 1]
            messages.insert(span[0], {"role": "user", "content": f"[历史摘要] {summary}"})
            changed = True
    return changed
