"""Agent API 包装器：调用现有 agent.py 执行任务，支持流式输出。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

AGENT_DIR = str(Path(__file__).resolve().parent.parent.parent)


def run_agent(task: str, workspace: str, on_output=None, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com") -> str:
    """运行 agent，支持流式输出回调。on_output(chunk) 会在每行输出时被调用。"""
    env = os.environ.copy()
    env["CHISEL_DESKTOP"] = "1"
    env["CHISEL_WORKSPACE"] = workspace
    cmd = [sys.executable, "-m", "agent", task, "--workspace", workspace, "--model", model, "--base-url", base_url, "--max-steps", "30"]

    full_output = []
    lock = threading.Lock()

    try:
        proc = subprocess.Popen(
            cmd, cwd=AGENT_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            env=env,
        )

        def read_stdout():
            for line in proc.stdout:
                with lock:
                    full_output.append(line)
                if on_output:
                    on_output(line)

        t = threading.Thread(target=read_stdout, daemon=True)
        t.start()
        proc.wait(timeout=300)
        t.join(timeout=5)

        with lock:
            raw = "".join(full_output)
        cleaned = _clean_output(raw)
        if cleaned:
            cleaned += f"\n\n> 工作目录：{workspace}"
        return cleaned

    except subprocess.TimeoutExpired:
        proc.kill()
        return "The agent took too long to respond. Please try a simpler task."
    except Exception as e:
        return f"An error occurred: {e}"


def _clean_output(text: str) -> str:
    """从 agent 的完整输出中提取用户可见的最终回答。"""
    # 1. 尝试提取 ✅ 任务完成 之后的内容
    if "✅ 任务完成" in text:
        parts = text.split("✅ 任务完成")
        last = parts[-1]
        lines = last.split("\n")
        content_lines = []
        started = False
        for line in lines:
            if not started:
                stripped = line.strip()
                if stripped and not stripped.startswith("=") and "共" not in stripped and "步" not in stripped:
                    started = True
                    content_lines.append(line)
            else:
                content_lines.append(line)
        if content_lines:
            cleaned = "\n".join(content_lines).strip()
            if cleaned:
                return cleaned

    # 2. 如果没有 ✅ 完成标记，显示所有输出但去掉调试前缀
    lines = text.split("\n")
    filtered = []
    for line in lines:
        stripped = line.strip()
        # 跳过调试行
        if stripped.startswith("[stderr]") or "CryptographyDeprecationWarning" in stripped:
            continue
        if stripped.startswith("[Plan mode:") or stripped.startswith("[步骤"):
            continue
        if stripped.startswith("🔧") or stripped.startswith("↳") or stripped.startswith("  ↳"):
            continue
        if stripped.startswith("⚠️") or stripped.startswith("⛔"):
            continue
        if stripped == "✅ 任务完成":
            continue
        # 保留 === 分隔线（只在有内容时保留一条）
        if stripped.startswith("===") and "完成" not in stripped:
            if filtered and filtered[-1].strip() != "---":
                filtered.append("---")
            continue
        # 保留空行和所有其他内容
        filtered.append(line)

    result = "\n".join(filtered).strip()
    return result if result else text[:5000]