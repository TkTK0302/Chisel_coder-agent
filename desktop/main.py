"""Chisel Desktop - Eel 桌面应用入口。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import eel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop.backend.database import Database
from desktop.backend.agent_wrapper import run_agent

DATA_DIR = Path.home() / ".chisel-desktop"
DATA_DIR.mkdir(parents=True, exist_ok=True)
db = Database(str(DATA_DIR / "chisel.db"))
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
eel.init(str(Path(__file__).resolve().parent / "renderer"))

@eel.expose
def list_projects():
    return json.dumps(db.list_projects())

@eel.expose
def select_folder():
    """打开文件夹选择对话框。"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="选择项目文件夹")
    root.destroy()
    return folder or ""

@eel.expose
def create_project(name: str, workspace_path: str = ""):
    if workspace_path:
        ws = workspace_path
    else:
        ws = str(PROJECTS_DIR / name.replace(" ", "_"))
    Path(ws).mkdir(parents=True, exist_ok=True)
    project = db.create_project(name, ws)
    db.create_conversation(project["id"], "New Chat")
    return json.dumps(project)

@eel.expose
def delete_project(project_id: str):
    p = db.get_project(project_id)
    if p:
        shutil.rmtree(p["workspace"], ignore_errors=True)
    db.delete_project(project_id)

@eel.expose
def update_workspace(project_id: str, new_workspace: str):
    p = db.get_project(project_id)
    if not p:
        return json.dumps({"error": "Not found"})
    Path(new_workspace).mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(DATA_DIR / "chisel.db"))
    conn.execute("UPDATE projects SET workspace=?, updated_at=? WHERE id=?",
                 (new_workspace, datetime.now(timezone.utc).isoformat(), project_id))
    conn.commit()
    conn.close()
    return json.dumps({"ok": True})

@eel.expose
def list_conversations(project_id: str):
    return json.dumps(db.list_conversations(project_id))

@eel.expose
def create_conversation(project_id: str, title: str = "New Chat"):
    return json.dumps(db.create_conversation(project_id, title))

@eel.expose
def delete_conversation(conv_id: str):
    db.delete_conversation(conv_id)

@eel.expose
def list_messages(conv_id: str):
    return json.dumps(db.list_messages(conv_id))

@eel.expose
def send_message(conv_id: str, content: str):
    db.add_message(conv_id, "user", content)
    project = None
    for p in db.list_projects():
        for c in db.list_conversations(p["id"]):
            if c["id"] == conv_id:
                project = p
                break
    if not project:
        return json.dumps({"error": "Conversation not found"})
    files = db.list_files(project["id"])
    file_context = ""
    if files:
        file_context = "\n\nProject files available:\n"
        for f in files:
            file_context += f"  - {f['filename']} ({f['filepath']})\n"
        file_context += "\nUse read_file with the path to access these files.\n"
    task = content + file_context

    # 创建占位消息
    placeholder = db.add_message(conv_id, "assistant", "⏳ Thinking...")
    msg_id = placeholder["id"]

    # 流式输出回调
    accumulated = []
    def on_output(chunk):
        accumulated.append(chunk)
        current = "".join(accumulated)
        # 推送到前端
        try:
            eel.update_message(msg_id, current)()
        except Exception:
            pass

    # 后台运行 agent
    import threading as th
    def run():
        try:
            result = run_agent(task, project["workspace"], on_output=on_output)
            # 保存最终结果
            import sqlite3
            conn = sqlite3.connect(str(Path.home() / ".chisel-desktop" / "chisel.db"))
            conn.execute("UPDATE messages SET content=? WHERE id=?", (result, msg_id))
            conn.commit()
            conn.close()
            # 通知前端完成
            try:
                eel.update_message(msg_id, result)()
                eel.message_done(msg_id)()
            except Exception:
                pass
        except Exception as e:
            try:
                eel.update_message(msg_id, f"Error: {e}")()
                eel.message_done(msg_id)()
            except Exception:
                pass

    th.Thread(target=run, daemon=True).start()
    return json.dumps({"role": "assistant", "content": "⏳ Thinking...", "id": msg_id})

@eel.expose
def list_files(project_id: str):
    return json.dumps(db.list_files(project_id))

@eel.expose
def upload_file(project_id: str, filename: str, content_base64: str):
    """上传文件（通过 base64 内容，兼容浏览器安全限制）。"""
    import base64
    p = db.get_project(project_id)
    if not p:
        return json.dumps({"error": "Not found"})
    ws = Path(p["workspace"])
    ws.mkdir(parents=True, exist_ok=True)
    dest = ws / filename
    data = base64.b64decode(content_base64)
    dest.write_bytes(data)
    size = len(data)
    db.add_file(project_id, filename, str(dest), size)
    return json.dumps({"filename": filename, "size": size})
    db.add_file(project_id, filename, str(dest), size)
    return json.dumps({"filename": filename, "size": size})

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9876
    print(f"Chisel Desktop starting on http://localhost:{port}", flush=True)
    eel.start("index.html", port=port, size=(1400, 900), position=(100, 50))