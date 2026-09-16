import logging
from typing import Optional

from torrent_sentinel.config import settings
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.engine.diagnostics import Diagnostics
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.vpn.base import BaseVPNAdapter

logger = logging.getLogger(__name__)

class SentinelDaemon:
    def __init__(self, vpn_adapter: BaseVPNAdapter):
        self.transmission = TransmissionClient()
        self.ollama = OllamaClient()
        self.storage = Storage()
        self.diagnostics = Diagnostics(self.transmission, self.ollama)
        self.decision_engine = DecisionEngine(self.diagnostics, self.storage, vpn_adapter)
        self.vpn_adapter = vpn_adapter
        self.running = False

    async def run(self):
        await self.storage.initialize()
        self.running = True
        logger.info("Torrent Sentinel Daemon started.")

        while self.running:
            try:
                # 1. Monitor
                torrents = await self.transmission.get_torrents()
                current_profile = await self.vpn_adapter.get_current_profile()

                # 2. Decide
                target_profile = await self.decision_engine.decide_rotation(torrents)

                # 3. Act
                if target_profile:
                    reason = "AI recommended rotation" 
                    success = await self.decision_engine.execute_rotation(current_profile, target_profile, reason)
                    if success:
                        logger.info(f"Successfully rotated to {target_profile.name}")
                    else:
                        logger.error("Rotation failed")

                # 4. Wait for next interval
                import asyncio
                await asyncio.sleep(60) 

            except Exception as e:
                logger.exception(f"Error in daemon loop: {e}")
                import asyncio
                await asyncio.sleep(30)

    def stop(self):
        self.running = False
