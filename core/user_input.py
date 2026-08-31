"""文件式 IPC：Agent 子进程与 Eel 前端之间的用户交互桥接。

Agent 需要用户输入时，把问题写到 .chisel/ask_question.json，
Eel 后端检测到后展示给用户，用户点击按钮后答案写入 .chisel/ask_answer.json，
Agent 读取答案继续执行。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def ask_question(workspace: str, question: str, options: list[str] | None = None) -> str:
    """向用户提问并等待回答。返回用户的选择。"""
    ask_dir = Path(workspace) / ".chisel"
    ask_dir.mkdir(parents=True, exist_ok=True)
    q_file = ask_dir / "ask_question.json"
    a_file = ask_dir / "ask_answer.json"

    # 清理旧文件
    q_file.unlink(missing_ok=True)
    a_file.unlink(missing_ok=True)

    # 写入问题
    q_id = str(time.time())
    data = {
        "id": q_id,
        "question": question,
        "options": options or ["y", "N"],
        "timestamp": time.time(),
    }
    q_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 等待回答（最多 600 秒）
    for _ in range(600):
        if a_file.exists():
            try:
                answer = json.loads(a_file.read_text(encoding="utf-8"))
                a_file.unlink(missing_ok=True)
                q_file.unlink(missing_ok=True)  # 删除问题文件
                return answer.get("answer", "N")
            except Exception:
                a_file.unlink(missing_ok=True)
                q_file.unlink(missing_ok=True)
                return "N"
        time.sleep(0.5)

    q_file.unlink(missing_ok=True)
    return "N"  # 超时默认拒绝


def get_pending_question(workspace: str) -> dict | None:
    """Eel 后端调用：检查是否有待处理的问题。"""
    q_file = Path(workspace) / ".chisel" / "ask_question.json"
    if q_file.exists():
        try:
            return json.loads(q_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def submit_answer(workspace: str, answer: str) -> None:
    """Eel 后端调用：提交用户回答。"""
    a_file = Path(workspace) / ".chisel" / "ask_answer.json"
    a_file.write_text(
        json.dumps({"answer": answer, "timestamp": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_question(workspace: str) -> None:
    """清理问题文件。"""
    (Path(workspace) / ".chisel" / "ask_question.json").unlink(missing_ok=True)
    (Path(workspace) / ".chisel" / "ask_answer.json").unlink(missing_ok=True)