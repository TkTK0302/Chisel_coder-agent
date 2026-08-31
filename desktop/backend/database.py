"""项目 + 对话 + 消息 的 SQLite 持久化。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init()

    def _init(self):
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                workspace TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS project_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)
        self._conn.commit()

    def list_projects(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def create_project(self, name: str, workspace: str) -> dict:
        pid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (pid, name, workspace, now, now))
        self._conn.commit()
        return {"id": pid, "name": name, "workspace": workspace, "created_at": now, "updated_at": now}

    def delete_project(self, project_id: str):
        self._conn.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE project_id=?)", (project_id,))
        self._conn.execute("DELETE FROM conversations WHERE project_id=?", (project_id,))
        self._conn.execute("DELETE FROM project_files WHERE project_id=?", (project_id,))
        self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        self._conn.commit()

    def list_conversations(self, project_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM conversations WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def create_conversation(self, project_id: str, title: str = "New Chat") -> dict:
        cid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, project_id, title, now, now))
        self._conn.commit()
        return {"id": cid, "project_id": project_id, "title": title, "created_at": now, "updated_at": now}

    def delete_conversation(self, conversation_id: str):
        self._conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        self._conn.commit()

    def list_messages(self, conversation_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)", (conversation_id, role, content, now))
        self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        self._conn.commit()
        return {"id": cur.lastrowid, "conversation_id": conversation_id, "role": role, "content": content, "created_at": now}

    def list_files(self, project_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM project_files WHERE project_id=? ORDER BY uploaded_at", (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_file(self, project_id: str, filename: str, filepath: str, size: int) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute("INSERT INTO project_files (project_id, filename, filepath, size, uploaded_at) VALUES (?,?,?,?,?)", (project_id, filename, filepath, size, now))
        self._conn.commit()
        return {"id": cur.lastrowid, "project_id": project_id, "filename": filename, "filepath": filepath, "size": size}

    def delete_file(self, file_id: int):
        self._conn.execute("DELETE FROM project_files WHERE id=?", (file_id,))
        self._conn.commit()