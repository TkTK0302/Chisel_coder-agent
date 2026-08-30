"""上下文管理：长输出截断 + 超限压缩（递归分治 + 跳过标记）。

设计来源：
  - 长命令输出的"头尾截断 + 完整内容保存到文件"：OpenHands maybe_truncate。
  - 超限时的"确定性折叠"：Cline basic-compaction。
  - 递归分治摘要：Aider ChatSummary（递归地把最老的一半对话摘要，深度 ≤ 3）。
  - 跳过标记：已压缩的消息不再重复压缩。
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
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
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
    if messages[i].get("role") != "assistant":
        return None
    calls = messages[i].get("tool_calls")
    if not calls:
        return None
    n = len(calls)
    if i + n >= len(messages):
        return None
    for j in range(1, n + 1):
        if messages[i + j].get("role") != "tool":
            return None
    return (i, i + n)


def _oldest_round_trip(messages: list[dict], pinned: int) -> tuple[int, int] | None:
    for i in range(pinned, len(messages)):
        span = _round_trip_span(messages, i)
        if span:
            return span
    return None


def _summarize_turn(msgs: list[dict], client, max_chars: int = 600) -> str:
    text = "\n".join(
        f"[{m.get('role')}] {(m.get('content') or '')[:1500]}" for m in msgs
    )
    try:
        resp = client.chat(
            [
                {
                    "role": "system",
                    "content": "Compress the following tool execution record into a brief summary. "
                               "Keep key decisions, errors, and results. Use concise language, max 600 chars.",
                },
                {"role": "user", "content": text},
            ]
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


FOLD_MAX_CHARS = 8000
TRIGGER_RATIO = 0.9  # 超过 90% 上限就触发压缩
TARGET_RATIO = 0.7   # 压缩到 70% 上限


def _span_size(messages: list[dict], span: tuple[int, int]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages[span[0] : span[1] + 1])


def _is_compacted(msg: dict) -> bool:
    """检查消息是否已被压缩（跳过标记）。"""
    return msg.get("compacted") is True


def compress_context(
    messages: list[dict],
    client,
    max_tokens: int,
    pinned: int = 3,
    max_summary_chars: int = 600,
) -> bool:
    """把 messages 原地压缩到 max_tokens 以内。

    改进：
      - 触发比例：超过 90% 上限才触发，压缩到 70%
      - 跳过标记：已压缩的消息不再重复压缩
      - 递归分治：如果折叠后仍超限，递归地摘要最老一半
    """
    estimate = client.estimate_messages_tokens(messages)
    threshold = int(max_tokens * TRIGGER_RATIO)
    target = int(max_tokens * TARGET_RATIO)

    if estimate <= threshold:
        return False
    changed = False

    # 1) 确定性折叠：删最老的"小"回合
    while client.estimate_messages_tokens(messages) > target:
        span = _oldest_round_trip(messages, pinned)
        if span is None:
            break
        if _span_size(messages, span) > FOLD_MAX_CHARS:
            break
        # 跳过已压缩的消息
        if any(_is_compacted(messages[i]) for i in range(span[0], span[1] + 1)):
            # 标记为已跳过，跳过这个回合
            messages[span[0]]["compacted_skipped"] = True
            break
        del messages[span[0] : span[1] + 1]
        changed = True
    if client.estimate_messages_tokens(messages) <= target:
        return changed

    # 2) 递归分治摘要：把最老的一半对话摘要（Aider ChatSummary 风格）
    # 找到 pinned 到当前长度的一半
    mid = pinned + (len(messages) - pinned) // 2
    # 确保 mid 对齐到完整回合边界
    while mid < len(messages):
        span = _round_trip_span(messages, mid)
        if span:
            mid = span[0]
            break
        mid += 1

    if mid > pinned:
        # 摘要 messages[pinned:mid]
        msgs_to_summarize = messages[pinned:mid]
        if msgs_to_summarize and not any(_is_compacted(m) for m in msgs_to_summarize):
            summary = _summarize_turn(msgs_to_summarize, client, max_summary_chars)
            if summary:
                del messages[pinned:mid]
                messages.insert(pinned, {
                    "role": "user",
                    "content": f"[History Summary] {summary}",
                    "compacted": True,  # 跳过标记
                })
                changed = True

    # 如果还超限，递归（最多 3 层深度）
    if client.estimate_messages_tokens(messages) > threshold and changed:
        return compress_context(messages, client, max_tokens, pinned, max_summary_chars)

    return changed