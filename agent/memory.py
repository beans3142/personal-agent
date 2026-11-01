"""Long-term facts and per-chat history in one SQLite file."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS history (chat TEXT PRIMARY KEY, items TEXT NOT NULL)")

    def save(self, text: str) -> int:
        if not text.strip():
            raise ValueError("fact is empty")
        with self.lock, self.db:
            return self.db.execute("INSERT INTO facts (text, created) VALUES (?, ?)", (text.strip(), time.time())).lastrowid

    def search(self, query: str, limit: int = 10) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def forget(self, fact_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    def load_history(self, chat: str) -> list[dict]:
        with self.lock:
            row = self.db.execute("SELECT items FROM history WHERE chat = ?", (chat,)).fetchone()
        return json.loads(row["items"]) if row else []

    def save_history(self, chat: str, items: list[dict]) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO history (chat, items) VALUES (?, ?) ON CONFLICT(chat) DO UPDATE SET items = excluded.items",
                (chat, json.dumps(items, ensure_ascii=False)),
            )
