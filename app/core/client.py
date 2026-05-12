"""Pyrogram client factory."""
from pyrogram import Client

from app.config.settings import settings


def build_client() -> Client:
    settings.validate()
    return Client(
        name="vidadi",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=settings.SESSION_STRING,
        in_memory=True,
        no_updates=False,
    )
