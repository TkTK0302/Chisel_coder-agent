"""Agent API 包装器：调用现有 agent.py 执行任务，提取用户可见的回答。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

AGENT_DIR = str(Path(__file__).resolve().parent.parent.parent)


def run_agent(task: str, workspace: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com") -> str:
    """运行 agent，返回只包含最终回答的文本（过滤掉调试信息）。"""
    env = os.environ.copy()
    cmd = [sys.executable, "-m", "agent", task, "--workspace", workspace, "--model", model, "--base-url", base_url, "--max-steps", "30"]
    try:
        result = subprocess.run(cmd, cwd=AGENT_DIR, capture_output=True, text=True, encoding="utf-8", timeout=300, env=env)
        raw = result.stdout or ""
        cleaned = _clean_output(raw)
        # 在回答末尾加上工作目录信息，让用户知道文件在哪
        if cleaned:
            return cleaned + f"\n\n> 工作目录：{workspace}"
        return cleaned
    except subprocess.TimeoutExpired:
        return "The agent took too long to respond. Please try a simpler task."
    except Exception as e:
        return f"An error occurred: {e}"


def _clean_output(text: str) -> str:
    """从 agent 的完整输出中提取用户可见的最终回答。"""
    # 1. 尝试提取 ✅ 任务完成 之后的内容
    if "✅ 任务完成" in text:
        parts = text.split("✅ 任务完成")
        last = parts[-1]
        # 去掉最后的 === 分隔线
        lines = last.split("\n")
        # 找到第一个非空行且不是 === 的行作为开始
        content_lines = []
        started = False
        for line in lines:
            if not started:
                if line.strip() and not line.strip().startswith("="):
                    started = True
                    content_lines.append(line)
            else:
                content_lines.append(line)
        if content_lines:
            cleaned = "\n".join(content_lines).strip()
            if cleaned:
                return cleaned

    # 2. 如果没有 ✅ 任务完成，尝试提取 summary 之后的内容
    if "**总结：**" in text or "**总结**" in text:
        parts = re.split(r"\*\*总结[：]?\*\*", text)
        if len(parts) > 1:
            return parts[-1].strip()

    # 3. 如果都没有，过滤掉明显的调试行
    lines = text.split("\n")
    filtered = []
    for line in lines:
        stripped = line.strip()
        # 跳过调试信息
        if not stripped:
            continue
        if stripped.startswith("[stderr]") or "CryptographyDeprecationWarning" in stripped:
            continue
        if stripped.startswith("[Plan mode:") or stripped.startswith("[步骤"):
            continue
        if stripped.startswith("🔧") or stripped.startswith("↳") or stripped.startswith("  ↳"):
            continue
        if stripped.startswith("⚠️") or stripped.startswith("⛔"):
            continue
        if stripped.startswith("===") and "完成" not in stripped:
            continue
        if stripped == "✅ 任务完成":
            continue
        filtered.append(line)

    return "\n".join(filtered).strip() if filtered else text[:5000]