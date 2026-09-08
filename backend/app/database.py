from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT, system_prompt TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, reasoning TEXT, usage_json TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat_created ON messages(chat_id, created_at);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT NOT NULL,
  progress_json TEXT NOT NULL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf_token TEXT NOT NULL,
  expires_at REAL NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            con.execute("UPDATE jobs SET state='failed', error='Control plane restarted during operation', updated_at=? WHERE state IN ('queued','running','cancelling')", (time.time(),))
            con.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connect() as con:
            con.execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def create_job(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        item = {"id": str(uuid.uuid4()), "kind": kind, "state": "queued", "payload": payload, "progress": {}, "error": None, "created_at": now, "updated_at": now}
        self.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (item["id"], kind, "queued", json.dumps(payload), "{}", None, now, now))
        return item

    def update_job(self, job_id: str, *, state: str | None = None, progress: dict | None = None, error: str | None = None) -> None:
        current = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not current:
            return
        self.execute("UPDATE jobs SET state=?, progress_json=?, error=?, updated_at=? WHERE id=?", (state or current["state"], json.dumps(progress if progress is not None else json.loads(current["progress_json"])), error, time.time(), job_id))

    def job(self, job_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            return None
        row["payload"] = json.loads(row.pop("payload_json"))
        row["progress"] = json.loads(row.pop("progress_json"))
        return row


db: Database | None = None


def init_database(data_dir: Path) -> Database:
    global db
    db = Database(data_dir / "freetoken-web.sqlite3")
    db.initialize()
    return db


def get_db() -> Database:
    if db is None:
        raise RuntimeError("database not initialized")
    return db

