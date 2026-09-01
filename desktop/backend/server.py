"""FastAPI 后端服务器。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from desktop.backend.database import Database
from desktop.backend.agent_wrapper import run_agent

app = FastAPI(title="Chisel Desktop")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = Path.home() / ".chisel-desktop"
DATA_DIR.mkdir(parents=True, exist_ok=True)
db = Database(str(DATA_DIR / "chisel.db"))
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/api/projects")
def list_projects():
    return db.list_projects()

class CreateProject(BaseModel):
    name: str

@app.post("/api/projects")
def create_project(body: CreateProject):
    ws = str(PROJECTS_DIR / body.name.replace(" ", "_"))
    Path(ws).mkdir(parents=True, exist_ok=True)
    project = db.create_project(body.name, ws)
    db.create_conversation(project["id"], "Chat 1")
    return project

@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    p = db.get_project(project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    shutil.rmtree(p["workspace"], ignore_errors=True)
    db.delete_project(project_id)
    return {"ok": True}

@app.get("/api/projects/{project_id}/conversations")
def list_conversations(project_id: str):
    return db.list_conversations(project_id)

class CreateConversation(BaseModel):
    title: str = "New Chat"

@app.post("/api/projects/{project_id}/conversations")
def create_conversation(project_id: str, body: CreateConversation):
    return db.create_conversation(project_id, body.title)

@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    db.delete_conversation(conv_id)
    return {"ok": True}

@app.get("/api/conversations/{conv_id}/messages")
def list_messages(conv_id: str):
    return db.list_messages(conv_id)

class SendMessage(BaseModel):
    content: str

@app.post("/api/conversations/{conv_id}/messages")
def send_message(conv_id: str, body: SendMessage):
    db.add_message(conv_id, "user", body.content)
    convs = []
    for p in db.list_projects():
        for c in db.list_conversations(p["id"]):
            if c["id"] == conv_id:
                convs.append((p, c))
    if not convs:
        raise HTTPException(404, "Conversation not found")
    project, conversation = convs[0]
    files = db.list_files(project["id"])
    file_context = ""
    if files:
        file_context = "\n\nProject files available:\n"
        for f in files:
            file_context += f"  - {f['filename']} ({f['filepath']})\n"
        file_context += "\nUse read_file with the path to access these files.\n"
    task = body.content + file_context
    result_container = {}
    def run():
        result_container["output"] = run_agent(task, project["workspace"])
    t = Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=310)
    output = result_container.get("output", "Agent timed out.")
    db.add_message(conv_id, "assistant", output)
    return {"role": "assistant", "content": output}

@app.get("/api/projects/{project_id}/files")
def list_files(project_id: str):
    return db.list_files(project_id)

@app.post("/api/projects/{project_id}/files")
async def upload_file(project_id: str, file: UploadFile):
    p = db.get_project(project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    ws = Path(p["workspace"])
    ws.mkdir(parents=True, exist_ok=True)
    dest = ws / file.filename
    content = await file.read()
    dest.write_bytes(content)
    db.add_file(project_id, file.filename, str(dest), len(content))
    return {"filename": file.filename, "path": str(dest), "size": len(content)}

@app.delete("/api/projects/files/{file_id}")
def delete_file(file_id: int):
    db.delete_file(file_id)
    return {"ok": True}

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Chisel backend starting on port {port}...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")