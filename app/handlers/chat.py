"""Group chat handler — replies with Vidadi personality.

Behaviour:
- Always processes mentions / replies to the userbot.
- Otherwise replies with low probability so the bot doesn't spam.
- Lowers reply probability while a VC session is active in the same chat.
- Tracks every message into memory.
"""
from __future__ import annotations

import asyncio
import random

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message

from app.config.settings import settings
from app.audio.vc_manager import VCManager
from app.memory import user_memory
from app.ai.brain import get_brain
from app.handlers.filters import RateLimiter
from app.core.logger import log


def register(client: Client, vc: VCManager) -> None:
    rl = RateLimiter(per_seconds=3.0)
    me_id_holder: dict[str, int] = {}

    async def _me_id() -> int:
        if "id" not in me_id_holder:
            me = await client.get_me()
            me_id_holder["id"] = me.id
        return me_id_holder["id"]

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

        me_id = await _me_id()
        is_reply_to_me = (
            m.reply_to_message
            and m.reply_to_message.from_user
            and m.reply_to_message.from_user.id == me_id
        )
        mentions_me = bool(re_match_name(text))

        # Reduce activity when busy in VC
        base_prob = settings.CHAT_REPLY_PROBABILITY
        if vc.is_in_vc(m.chat.id):
            base_prob *= 0.3

        triggered = is_reply_to_me or mentions_me or (random.random() < base_prob)
        if not triggered:
            return
        if not rl.allow(m.from_user.id):
            return

        # Human-like typing delay
        try:
            await client.send_chat_action(m.chat.id, "typing")
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.8, 2.4))

        try:
            history = await user_memory.context_for(m.chat.id, limit=10)
            brain = get_brain()
            reply = await brain.reply(text, speaker_name, history, in_voice_chat=False)
            if not reply:
                return
            # Occasionally reply, occasionally just send
            if is_reply_to_me or mentions_me or random.random() < 0.5:
                await m.reply_text(reply, quote=True)
            else:
                await client.send_message(m.chat.id, reply)
        except Exception as e:
            log.exception("chat reply failed: {}", e)


_NAME_PATTERNS = ("vidadi", "Vidadi", "VIDADI", "vido", "vidos")


def re_match_name(text: str) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in _NAME_PATTERNS)
