"""Aggregates handler registration."""
from pyrogram import Client

from app.audio.vc_manager import VCManager
from app.handlers import commands, chat, voice_chat


def register_all_handlers(client: Client, vc: VCManager) -> None:
    commands.register(client, vc)
    voice_chat.register(client, vc)
    chat.register(client, vc)
