"""Owner commands: .ses (join VC), .bye (leave), .status, .say, .forget."""
from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.filters import owner_only
from app.audio.vc_manager import VCManager
from app.core.logger import log


def register(client: Client, vc: VCManager) -> None:

    @client.on_message(owner_only() & filters.command("ses", prefixes="."))
    async def cmd_ses(_, m: Message):
        if m.chat.type.name == "PRIVATE":
            await m.reply_text("ala bu özəl chatdı, qrupda yaz.", quote=True)
            return
        await m.reply_text("həə gəldim brat...", quote=True)
        ok = await vc.join(m.chat.id)
        if not ok:
            await m.reply_text(
                "blet alınmadı, voice chat aktivdi? Owner-ə admin verirsiz?",
                quote=True,
            )
            return
        cap = vc._captures.get(m.chat.id)  # noqa: SLF001
        strategy = cap.strategy if cap else "noop"
        if strategy in ("native_callback", "record_stream_fifo", "ffmpeg_loopback"):
            await m.reply_text(
                "içerdəyəm artıq, danışın görüm 🙂 (realtime listening: aktiv)",
                quote=True,
            )
        else:
            await m.reply_text(
                "içerdəyəm artıq 🙂 — amma realtime listening hələ tam bağlanmayıb, "
                "PyTgCalls inbound hook bu versiyada məhduddur.",
                quote=True,
            )

    @client.on_message(owner_only() & filters.command("bye", prefixes="."))
    async def cmd_bye(_, m: Message):
        await vc.leave(m.chat.id)
        await m.reply_text("getdim, sora görüşürük.", quote=True)

    @client.on_message(owner_only() & filters.command("status", prefixes="."))
    async def cmd_status(_, m: Message):
        in_vc = vc.is_in_vc(m.chat.id)
        cap = vc._captures.get(m.chat.id)  # noqa: SLF001
        pipe = vc.active_chats.get(m.chat.id)
        lines = [
            f"VC: {'aktiv' if in_vc else 'yox'}",
            f"qruplar: {len(vc.active_chats)}",
        ]
        if cap:
            lines.append(f"inbound: {cap.strategy}")
        if pipe:
            lines.append(f"phase: {pipe.state.phase.value}")
        await m.reply_text("\n".join(lines), quote=True)

    @client.on_message(owner_only() & filters.command("say", prefixes="."))
    async def cmd_say(_, m: Message):
        text = " ".join(m.command[1:]) if len(m.command) > 1 else ""
        if not text:
            await m.reply_text("nə deyim brat? `.say <söz>`", quote=True)
            return
        if not vc.is_in_vc(m.chat.id):
            await m.reply_text("VC-də deyiləm, əvvəlcə `.ses`", quote=True)
            return
        await vc.speak(m.chat.id, text)

    @client.on_message(owner_only() & filters.command("stop", prefixes="."))
    async def cmd_stop(_, m: Message):
        """Force-interrupt current bot playback (drain queue + cut audio)."""
        if not vc.is_in_vc(m.chat.id):
            return
        await vc.interrupt(m.chat.id)
        await m.reply_text("ok susdum.", quote=True)

    log.info("commands registered")
