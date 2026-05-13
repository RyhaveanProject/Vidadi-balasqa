"""User-level memory helpers (names, facts, message context).

Voice-chat specific additions:
* ``vc_context_for`` — formats the recent history with a small
  speaker map so the LLM gets "X əvvəl bunları dedi" baseline.
* ``link_voice_to_user`` — associate a fingerprint-identified
  speaker with their full Telegram user info from prior text chat.
"""
from __future__ import annotations

import time
from typing import List, Tuple

from app.memory import db


async def remember_user(user_id: int, name: str, username: str | None = None) -> None:
    await db.upsert_user(user_id, name, username, int(time.time()))


async def remember_message(chat_id: int, user_id: int, name: str, text: str) -> None:
    await db.log_message(chat_id, user_id, name, text, int(time.time()))


async def context_for(chat_id: int, limit: int = 12) -> List[Tuple[str, str]]:
    return await db.recent_messages(chat_id, limit)


async def vc_context_for(
    chat_id: int,
    current_speaker: str,
    limit: int = 12,
) -> List[Tuple[str, str]]:
    """Voice-chat-aware context: same shape as ``context_for`` but
    annotated with a synthesised header for the speaker so the LLM
    knows who is on the mic right now.
    """
    recent = await db.recent_messages(chat_id, limit)
    return recent + [("[system]", f"Hazırda mikrofonda: {current_speaker}")]


async def is_known(user_id: int) -> bool:
    summary = await db.get_user_summary(user_id)
    return bool(summary and summary["msg_count"] > 3)


async def msg_count(user_id: int) -> int:
    s = await db.get_user_summary(user_id)
    return s["msg_count"] if s else 0
