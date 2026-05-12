"""SQLite memory store — users, messages, voice samples."""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

import aiosqlite

from app.config.settings import settings
from app.core.logger import log


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    name       TEXT,
    username   TEXT,
    notes      TEXT DEFAULT '',
    msg_count  INTEGER DEFAULT 0,
    last_seen  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    user_id    INTEGER,
    name       TEXT,
    text       TEXT,
    ts         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);

CREATE TABLE IF NOT EXISTS voice_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    name       TEXT,
    embedding  BLOB,
    duration   REAL,
    ts         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_samples(user_id);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    fact       TEXT,
    ts         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
"""


async def init_db() -> None:
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    log.info("SQLite ready at {}", settings.DB_PATH)


async def upsert_user(user_id: int, name: str, username: Optional[str], ts: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """INSERT INTO users(user_id, name, username, msg_count, last_seen)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   name=excluded.name,
                   username=excluded.username,
                   msg_count = users.msg_count + 1,
                   last_seen=excluded.last_seen""",
            (user_id, name, username or "", ts),
        )
        await db.commit()


async def log_message(chat_id: int, user_id: int, name: str, text: str, ts: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages(chat_id, user_id, name, text, ts) VALUES (?,?,?,?,?)",
            (chat_id, user_id, name, text, ts),
        )
        await db.commit()


async def recent_messages(chat_id: int, limit: int = 12) -> List[Tuple[str, str]]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT name, text FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
    return list(reversed(rows))


async def get_user_summary(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT name, username, msg_count, last_seen, notes FROM users WHERE user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "name": row[0],
            "username": row[1],
            "msg_count": row[2],
            "last_seen": row[3],
            "notes": row[4],
        }


async def add_fact(user_id: int, fact: str, ts: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO facts(user_id, fact, ts) VALUES (?,?,?)",
            (user_id, fact, ts),
        )
        await db.commit()
