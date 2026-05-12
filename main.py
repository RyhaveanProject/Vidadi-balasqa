"""
Vidadi AI Voice Userbot
Owner: Raven
Entry point — boots Pyrogram client, PyTgCalls, registers handlers, runs forever.
"""
import asyncio
import signal
import sys

from app.core.logger import setup_logger, log
from app.config.settings import settings
from app.core.client import build_client
from app.audio.vc_manager import VCManager
from app.memory.db import init_db
from app.handlers import register_all_handlers
from app.services.reconnect import ReconnectGuard


async def _amain() -> None:
    setup_logger()
    log.info("=" * 60)
    log.info("  Vidadi AI Voice Userbot — booting up")
    log.info("  Owner: Raven (id=%s)", settings.OWNER_ID)
    log.info("=" * 60)

    await init_db()

    app_client = build_client()
    vc = VCManager(app_client)

    register_all_handlers(app_client, vc)

    guard = ReconnectGuard(app_client, vc)

    stop_event = asyncio.Event()

    def _signal_handler(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda *_: _signal_handler())

    await app_client.start()
    await vc.start()
    log.info("Userbot online. Owner can type `.ses` in any group to join VC.")

    asyncio.create_task(guard.run())

    await stop_event.wait()

    log.info("Stopping services...")
    await vc.stop()
    await app_client.stop()
    log.info("Bye.")


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
