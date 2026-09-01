"""上下文管理：长输出截断 + 滑动窗口压缩 + 关键点提取。

核心机制：
  1. 关键点记忆（KeyPointMemory）：每轮提取 决策/约束/待办，结构化存储
  2. 行级截断（truncate_output）：保留头尾行，不破坏代码块
  3. 滑动窗口压缩（compress_context）：保护用户消息 + 最近 N 轮完整 + 远期摘要
  4. 动态预算：当前轮不设硬上限，压缩时优先压缩最早历史
  5. 里程碑摘要：每 5 轮全量回顾，重置累积失真
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# ==============================================================================
# 关键点记忆（方案 C：模板化提取）
# ==============================================================================

KEY_POINT_TEMPLATE = """从以下对话中提取关键信息，严格按照模板输出：

## 决策
- [用户明确拍板的技术选型/方案选择，如无则写"无"]

## 约束
- [新增的架构限制/不能碰的模块/硬性要求，如无则写"无"]

## 待办
- [用户明确要求但尚未完成的事，如无则写"无"]

规则：
1. 只记录用户明确说过的内容，不要推测
2. 每条一行，用「- 」开头
3. 如果某类没有内容，写「- 无」
4. 不要输出模板以外的任何文字"""


class KeyPointMemory:
    """结构化关键点记忆，每轮对话结束后提取。

    保留最近 max_points 条关键点，更早的合并到里程碑摘要中。
    """

    def __init__(self, max_points: int = 10):
        self.decisions: list[tuple[int, str]] = []    # [(round_num, text)]
        self.constraints: list[tuple[int, str]] = []
        self.todos: list[tuple[int, str]] = []
        self.max_points = max_points
        self._turn_counter = 0
        self._milestone: str = ""  # 里程碑摘要

    def extract(self, turn_messages: list[dict], client) -> bool:
        """从最近一轮对话中提取关键点。"""
        self._turn_counter += 1
        # 只取本轮的用户消息和 assistant 最终回复
        text = "\n".join(
            f"[{m.get('role')}] {(m.get('content') or '')[:2000]}"
            for m in turn_messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        )
        if not text.strip():
            return False

        try:
            resp = client.chat([
                {"role": "system", "content": KEY_POINT_TEMPLATE},
                {"role": "user", "content": text},
            ])
            raw = (resp.choices[0].message.content or "").strip()
        except Exception:
            return False

        # 解析结构化输出
        section = None
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("## 决策"):
                section = "decisions"
            elif line.startswith("## 约束"):
                section = "constraints"
            elif line.startswith("## 待办"):
                section = "todos"
            elif line.startswith("- ") and section and line[2:].strip() != "无":
                item = line[2:].strip()
                target = getattr(self, section)
                target.append((self._turn_counter, item))

        # 超限时合并最老的到里程碑
        self._compact_if_needed()
        return True

    def _compact_if_needed(self):
        """关键点超过上限时，合并最老的 5 条到里程碑摘要。"""
        all_points = self.decisions + self.constraints + self.todos
        if len(all_points) <= self.max_points:
            return

        # 找到最老的 turn
        oldest_turn = min(
            (p[0] for p in all_points),
            default=self._turn_counter,
        )
        # 合并最老 5 条
        merged = []
        for attr in ("decisions", "constraints", "todos"):
            target = getattr(self, attr)
            old = [p for p in target if p[0] <= oldest_turn + 5]
            for p in old:
                merged.append(f"[轮次{p[0]}] {p[1]}")
            # 保留新的
            setattr(self, attr, [p for p in target if p[0] > oldest_turn + 5])

        if merged:
            self._milestone = "历史关键点：\n" + "\n".join(merged[-15:])

    def to_text(self) -> str:
        """格式化为注入 system prompt 的文本。"""
        parts = []
        if self._milestone:
            parts.append(f"## 里程碑\n{self._milestone}")

        sections = [
            ("## 决策", self.decisions),
            ("## 约束", self.constraints),
            ("## 待办", self.todos),
        ]
        for title, items in sections:
            if items:
                # 只显示最近 5 条
                recent = items[-5:]
                lines = "\n".join(f"- {text}" for _, text in recent)
                parts.append(f"{title}\n{lines}")

        return "\n\n".join(parts) if parts else ""


# ==============================================================================
# 行级截断（保留头尾行，不破坏代码/JSON 结构）
# ==============================================================================

def truncate_output(
    text: str,
    head_lines: int = 20,
    tail_lines: int = 20,
    save_dir: str | None = None,
    tool_prefix: str = "output",
) -> str:
    """行级截断：保留前 head_lines 行 + 后 tail_lines 行。

    相比字符截断，行级截断不会切在代码或 JSON 中间。
    截断位置插入文件路径提示，Agent 如需完整内容可 read_file 读取。
    """
    if not isinstance(text, str):
        text = str(text)

    lines = text.splitlines()
    total = len(lines)
    if total <= head_lines + tail_lines:
        return text

    file_path = None
    if save_dir:
        trunc_dir = Path(save_dir) / ".chisel" / "tool_outputs"
        trunc_dir.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        filename = f"{tool_prefix}_{content_hash}.log"
        save_path = trunc_dir / filename
        if not save_path.exists():
            save_path.write_text(text, encoding="utf-8")
        file_path = str(save_path)

    omitted = total - head_lines - tail_lines
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    if file_path:
        notice = (
            f"\n\n... 中间 {omitted} 行已截断，完整内容见 {file_path}\n"
            f"（如需查看完整内容，请使用 read_file 读取上述路径）\n"
        )
    else:
        notice = f"\n\n... 中间 {omitted} 行已截断 ...\n"

    return head + notice + tail


# ==============================================================================
# 滑动窗口压缩
# ==============================================================================

FOLD_MAX_CHARS = 8000
TRIGGER_RATIO = 0.85
TARGET_RATIO = 0.65


def _round_trip_span(messages: list[dict], i: int) -> tuple[int, int] | None:
    """找到从 i 开始的完整 assistant(tool_calls) + tool_results 回合。"""
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


def _find_user_messages(messages: list[dict]) -> list[int]:
    """找到所有用户消息的索引（作为保护锚点）。"""
    return [i for i, m in enumerate(messages) if m.get("role") == "user"]


def _span_size(messages: list[dict], span: tuple[int, int]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages[span[0]: span[1] + 1])


def _summarize_turn(msgs: list[dict], client, max_chars: int = 600) -> str:
    text = "\n".join(
        f"[{m.get('role')}] {(m.get('content') or '')[:1200]}" for m in msgs
    )
    try:
        resp = client.chat([
            {
                "role": "system",
                "content": (
                    "Compress the following conversation into a brief summary. "
                    "Keep: user requests, key decisions, errors encountered, and final results. "
                    f"Use concise language, max {max_chars} chars."
                ),
            },
            {"role": "user", "content": text},
        ])
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def compress_context(
    messages: list[dict],
    client,
    max_tokens: int,
    window_size: int = 3,
    key_memory: KeyPointMemory | None = None,
    max_summary_chars: int = 600,
) -> bool:
    """滑动窗口压缩：保护用户消息 + 最近 window_size 轮完整 + 远期 LLM 摘要。

    策略：
      1. 找到所有用户消息作为锚点，永不被删
      2. 保留最近 window_size 个用户消息对应的完整回合
      3. 更早的回合：小回合直接删除，大回合 LLM 摘要
      4. 动态预算：当前轮不设硬上限，压缩时优先压缩最早历史
      5. 每 5 轮摘要后，重新生成里程碑摘要避免累积失真
    """
    estimate = client.estimate_messages_tokens(messages)
    threshold = int(max_tokens * TRIGGER_RATIO)
    target = int(max_tokens * TARGET_RATIO)

    if estimate <= threshold:
        return False

    changed = False
    user_indices = _find_user_messages(messages)
    # 保护所有用户消息 + 最近 window_size 个用户回合
    protected = set(user_indices)
    if window_size > 0 and len(user_indices) >= window_size:
        # 最近 window_size 个用户消息的回合全部保护
        recent_users = user_indices[-window_size:]
        for ui in recent_users:
            # 保护这个用户消息到下一个用户消息之间的所有消息
            end = len(messages)
            for uj in user_indices:
                if uj > ui:
                    end = uj
                    break
            for k in range(ui, end):
                protected.add(k)

    # 1) 删除最老的非保护回合（小回合直接删）
    depth = 0
    while client.estimate_messages_tokens(messages) > target and depth < 3:
        depth += 1
        deleted = False
        for i in range(2, len(messages)):  # 跳过 system prompt 和 plan
            if i in protected:
                continue
            span = _round_trip_span(messages, i)
            if span is None:
                continue
            if _span_size(messages, span) > FOLD_MAX_CHARS:
                continue
            if any(messages[j].get("compacted") for j in range(span[0], span[1] + 1)):
                continue
            del messages[span[0]: span[1] + 1]
            # 更新受保护索引
            protected = {p - (span[1] - span[0] + 1) if p > span[1] else p for p in protected}
            changed = True
            deleted = True
            break
        if not deleted:
            break

    if client.estimate_messages_tokens(messages) <= target:
        return changed

    # 2) 摘要最老的非保护区域
    # 找到第一个非保护的工具回合区域
    start = 2
    while start < len(messages) and start in protected:
        start += 1

    # 找到摘要的结束位置：第一个受保护的用户消息之前
    end = start
    for ui in sorted(user_indices):
        if ui > start and ui in protected:
            end = ui
            break
    if end <= start:
        end = min(start + 20, len(messages))

    if end > start + 1:  # 至少 2 条消息（1 个 assistant + 1 个 tool）
        msgs_to_summarize = messages[start:end]
        if not any(m.get("compacted") for m in msgs_to_summarize):
            summary = _summarize_turn(msgs_to_summarize, client, max_summary_chars)
            if summary:
                # 如果有关键点记忆，合并到摘要中
                if key_memory and key_memory._milestone:
                    summary = key_memory._milestone + "\n\n近期对话摘要：" + summary

                del messages[start:end]
                messages.insert(start, {
                    "role": "user",
                    "content": f"[对话摘要] {summary}",
                    "compacted": True,
                })
                changed = True

    # 如果还超限，递归
    if client.estimate_messages_tokens(messages) > threshold and changed:
        return compress_context(
            messages, client, max_tokens, window_size, key_memory, max_summary_chars,
        )

    return changed