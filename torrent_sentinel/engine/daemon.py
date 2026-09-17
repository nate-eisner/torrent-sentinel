import asyncio
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

        logger.info("=======================================================")
        logger.info("  🚀 Torrent Sentinel Daemon Starting Up")
        logger.info("=======================================================")
        logger.info("Transmission Host    : %s:%d (path: %s)", settings.TRANSMISSION_HOST, settings.TRANSMISSION_PORT, settings.TRANSMISSION_RPC_PATH)
        logger.info("Ollama Diagnostics   : enabled=%s (endpoint: %s, model: %s)", settings.OLLAMA_ENABLED, settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL)
        logger.info("VPN Adapter Type     : %s (interface: %s)", settings.VPN_TYPE, settings.VPN_INTERFACE)
        logger.info("VPN Configs Dir      : %s", settings.VPN_CONFIGS_DIR)
        logger.info("Stalled Thresholds   : min_seeds=%d, min_rate=%.1f KB/s, stalled_duration=%d min", settings.MIN_SEEDS, settings.MIN_DOWNLOAD_RATE_KBPS, settings.STALLED_DURATION_MINUTES)
        logger.info("Anti-Flapping Rules  : cooldown=%d min, hourly_cap=%d, post_grace=%d min", settings.ROTATION_COOLDOWN_MINUTES, settings.MAX_ROTATIONS_PER_HOUR, settings.POST_ROTATION_GRACE_PERIOD_MINUTES)
        logger.info("Logging Level        : %s", settings.LOG_LEVEL)
        logger.info("=======================================================")

        # Run initial test connections
        if settings.OLLAMA_ENABLED:
            await self.ollama.health_check()

        available_vpns = await self.vpn_adapter.get_available_locations()
        logger.info("Discovered %d initial VPN location profile(s).", len(available_vpns))

        # Ensure VPN gateway tunnel is active on startup
        current_profile = await self.vpn_adapter.get_current_profile()
        if not current_profile and available_vpns:
            initial_target = available_vpns[0]
            top_locations = await self.storage.get_top_locations(limit=1)
            if top_locations:
                fav = next((p for p in available_vpns if p.id == top_locations[0]), None)
                if fav:
                    initial_target = fav
            logger.info("No active VPN tunnel detected on startup. Initializing gateway tunnel to: '%s'", initial_target.name)
            init_success = await self.vpn_adapter.rotate_to(initial_target)
            if init_success:
                logger.info("Initial VPN gateway tunnel established to '%s'", initial_target.name)
            else:
                logger.warning("Failed to establish initial VPN gateway tunnel to '%s'", initial_target.name)

        cycle = 0
        while self.running:
            cycle += 1
            logger.info("--- Monitoring Cycle #%d ---", cycle)
            try:
                # 1. Monitor
                torrents = await self.transmission.get_torrents()
                current_profile = await self.vpn_adapter.get_current_profile()
                current_loc_name = current_profile.name if current_profile else "Unknown / Default"
                
                logger.info(
                    "Cycle #%d Status: %d active torrent(s) found | Current VPN: '%s'",
                    cycle, len(torrents), current_loc_name
                )

                # 2. Decide
                target_profile = await self.decision_engine.decide_rotation(torrents)

                # 3. Act
                if target_profile:
                    reason = f"Automated rotation triggered on cycle #{cycle}"
                    logger.warning("Triggering rotation to '%s': %s", target_profile.name, reason)
                    success = await self.decision_engine.execute_rotation(current_profile, target_profile, reason)
                    if success:
                        logger.info("Cycle #%d: Successfully rotated to '%s'", cycle, target_profile.name)
                    else:
                        logger.error("Cycle #%d: Rotation to '%s' failed!", cycle, target_profile.name)
                else:
                    logger.info("Cycle #%d: System healthy or rotation not indicated. Standby.", cycle)

                # 4. Wait for next interval
                logger.debug("Cycle #%d complete. Sleeping for 60 seconds...", cycle)
                await asyncio.sleep(60)

            except Exception as e:
                logger.error("Exception occurred in daemon cycle #%d: %s", cycle, e, exc_info=True)
                logger.info("Pausing 30 seconds before retrying daemon cycle...")
                await asyncio.sleep(30)

    def stop(self):
        logger.info("Stopping Torrent Sentinel Daemon...")
        self.running = False
