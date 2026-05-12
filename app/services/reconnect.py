"""Background reconnect / health-check guard."""
from __future__ import annotations

import asyncio

from pyrogram import Client

from app.core.logger import log


class ReconnectGuard:
    def __init__(self, client: Client, vc) -> None:
        self.client = client
        self.vc = vc

    async def run(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                if not self.client.is_connected:
                    log.warning("client disconnected — attempting reconnect")
                    try:
                        await self.client.start()
                    except Exception as e:
                        log.error("reconnect failed: {}", e)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("guard tick error: {}", e)
