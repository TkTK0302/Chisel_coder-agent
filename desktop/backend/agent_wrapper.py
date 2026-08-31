"""Agent API 包装器：调用现有 agent.py 执行任务。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

AGENT_DIR = str(Path(__file__).resolve().parent.parent.parent)

def run_agent(task: str, workspace: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com") -> str:
    env = os.environ.copy()
    cmd = [sys.executable, "-m", "agent", task, "--workspace", workspace, "--model", model, "--base-url", base_url, "--max-steps", "30"]
    try:
        result = subprocess.run(cmd, cwd=AGENT_DIR, capture_output=True, text=True, encoding="utf-8", timeout=300, env=env)
        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr[-2000:]}"
        return output or "Agent produced no output."
    except subprocess.TimeoutExpired:
        return "Agent timed out (300s)."
    except Exception as e:
        return f"Agent error: {e}"