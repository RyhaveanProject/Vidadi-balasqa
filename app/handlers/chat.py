"""Group chat handler — replies with Vidadi personality.

Behaviour:
- Reply ONLY when:
    a) someone replies directly to one of Vidadi's messages, OR
    b) the message mentions Vidadi by name (text mention or @username).
- Otherwise stays silent (no random replies — owner request).
- Tracks every message into memory for context.
"""
from __future__ import annotations

import asyncio
import random
import re

from pyrogram import Client, filters
from pyrogram.enums import ChatType, MessageEntityType
from pyrogram.types import Message

from app.audio.vc_manager import VCManager
from app.memory import user_memory
from app.ai.brain import get_brain
from app.handlers.filters import RateLimiter
from app.core.logger import log


# Name aliases that count as a mention of Vidadi.
_NAME_ALIASES = ("vidadi", "vido", "vidos", "vidadicik", "vidadiy")
_WORD_RE = re.compile(r"[a-zəğıöşüçA-ZƏĞİÖŞÜÇ0-9]+")


def _mentions_by_name(text: str) -> bool:
    """True if any whole word in text matches a Vidadi alias."""
    if not text:
        return False
    low = text.lower()
    # quick substring prefilter
    if not any(a in low for a in _NAME_ALIASES):
        return False
    # whole-word check to avoid false positives like "individual"
    for word in _WORD_RE.findall(low):
        if word in _NAME_ALIASES:
            return True
    return False


def _mentions_by_entity(m: Message, my_id: int, my_username: str | None) -> bool:
    """True if Telegram entities reference the userbot (text_mention or @username)."""
    entities = (m.entities or []) + (m.caption_entities or [])
    text = m.text or m.caption or ""
    for ent in entities:
        if ent.type == MessageEntityType.TEXT_MENTION and ent.user and ent.user.id == my_id:
            return True
        if ent.type == MessageEntityType.MENTION and my_username:
            chunk = text[ent.offset:ent.offset + ent.length].lstrip("@").lower()
            if chunk == my_username.lower():
                return True
    return False


def register(client: Client, vc: VCManager) -> None:
    rl = RateLimiter(per_seconds=2.0)
    me_cache: dict[str, object] = {}

    async def _me() -> tuple[int, str | None]:
        if "id" not in me_cache:
            me = await client.get_me()
            me_cache["id"] = me.id
            me_cache["username"] = me.username
        return me_cache["id"], me_cache.get("username")

    @client.on_message(filters.text & ~filters.bot & ~filters.me)
    async def on_text(_, m: Message):
        if not m.from_user or not m.chat:
            return
        if m.chat.type == ChatType.PRIVATE:
            return  # ignore DMs
        text = (m.text or "").strip()
        if not text:
            return

        speaker_name = (m.from_user.first_name or "kimsə").strip()

        # Always remember
        try:
            await user_memory.remember_user(
                m.from_user.id, speaker_name, m.from_user.username
            )
            await user_memory.remember_message(m.chat.id, m.from_user.id, speaker_name, text)
        except Exception as e:
            log.warning("memory store error: {}", e)

        my_id, my_username = await _me()

        is_reply_to_me = bool(
            m.reply_to_message
            and m.reply_to_message.from_user
            and m.reply_to_message.from_user.id == my_id
        )
        mentions_me = _mentions_by_entity(m, my_id, my_username) or _mentions_by_name(text)

        # OWNER REQUEST: reply ONLY on direct reply or name mention.
        if not (is_reply_to_me or mentions_me):
            return

        if not rl.allow(m.from_user.id):
            return

        # Human-like typing delay
        try:
            await client.send_chat_action(m.chat.id, "typing")
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.6, 1.8))

        try:
            history = await user_memory.context_for(m.chat.id, limit=10)
            brain = get_brain()
            reply = await brain.reply(text, speaker_name, history, in_voice_chat=False)
            if not reply:
                return
            # Always reply with quote so the user sees the context.
            await m.reply_text(reply, quote=True)
        except Exception as e:
            log.exception("chat reply failed: {}", e)
