"""Bot tables: settings (on/off switch, style blob) and reply log.

Plain SQL that runs on SQLite, Postgres and CockroachDB (no SERIAL, no triggers).
DATABASE_URL set -> Postgres/CockroachDB via psycopg, else SQLite at BOT_SQLITE (default data/bot.db).
"""
import os
import sqlite3
import time
import uuid
from contextlib import closing

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS bot_replies (id TEXT PRIMARY KEY, chat TEXT NOT NULL, incoming TEXT NOT NULL,"
    " reply TEXT NOT NULL, ts BIGINT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS bot_replies_ts ON bot_replies (ts)",
]


class DB:
    def __init__(self, url: str | None = None, sqlite_path: str | None = None):
        self.url = url if url is not None else os.getenv("DATABASE_URL", "")
        self.sqlite_path = sqlite_path or os.getenv("BOT_SQLITE", "data/bot.db")
        for sql in SCHEMA:
            self._run(sql)
        try:  # added later; no "ADD COLUMN IF NOT EXISTS" on SQLite, so just ignore "already exists"
            self._run("ALTER TABLE bot_replies ADD COLUMN name TEXT")
        except Exception:  # noqa: BLE001
            pass

    def _connect(self):
        if self.url:
            import psycopg
            return psycopg.connect(self.url, autocommit=True)
        os.makedirs(os.path.dirname(self.sqlite_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.sqlite_path)
        conn.isolation_level = None  # autocommit
        return conn

    # ponytail: new connection per query; fine at a few queries/minute, pool if traffic grows
    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        if self.url:
            sql = sql.replace("?", "%s")
        with closing(self._connect()) as conn:
            cur = conn.execute(sql, args)
            return cur.fetchall() if cur.description else []

    def get(self, key: str, default: str | None = None) -> str | None:
        rows = self._run("SELECT value FROM bot_settings WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set(self, key: str, value: str) -> None:
        # INSERT ... ON CONFLICT works on SQLite 3.24+, Postgres and CockroachDB
        self._run("INSERT INTO bot_settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))

    def is_on(self) -> bool:
        return self.get("switch", "off") == "on"

    def log_reply(self, chat: str, incoming: str, reply: str, name: str = "") -> None:
        self._run("INSERT INTO bot_replies (id, chat, name, incoming, reply, ts) VALUES (?, ?, ?, ?, ?, ?)",
                  (uuid.uuid4().hex, chat, name, incoming, reply, int(time.time())))

    def recent_replies(self, limit: int = 200) -> list[tuple]:
        """(ts, chat, name, incoming, reply), newest first."""
        return self._run("SELECT ts, chat, name, incoming, reply FROM bot_replies ORDER BY ts DESC LIMIT ?", (limit,))

    def replies_since(self, seconds: int) -> int:
        return self._run("SELECT COUNT(*) FROM bot_replies WHERE ts >= ?", (int(time.time()) - seconds,))[0][0]
