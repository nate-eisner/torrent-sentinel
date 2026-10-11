import asyncio
import logging
from typing import Optional

import httpx

from datetime import datetime, timezone

from torrent_sentinel.config import settings
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.services.tracker_service import TrackerService
from torrent_sentinel.engine.booster import TorrentBooster
from torrent_sentinel.engine.diagnostics import Diagnostics
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.engine.autopilot import AutopilotEngine
from torrent_sentinel.vpn.base import BaseVPNAdapter
from torrent_sentinel.models import AutopilotMode, OffVpnState

logger = logging.getLogger(__name__)

class SentinelDaemon:
    def __init__(self, vpn_adapter: BaseVPNAdapter):
        self.transmission = TransmissionClient()
        self.storage = Storage()
        self.ollama = OllamaClient(storage=self.storage)
        self.tracker_service = TrackerService(settings.TRACKER_LIST_URLS)
        self.diagnostics = Diagnostics(self.transmission, self.ollama)
        self.decision_engine = DecisionEngine(self.diagnostics, self.storage, vpn_adapter)
        self.booster = TorrentBooster(
            self.transmission,
            self.tracker_service,
            self.storage,
            self.decision_engine.notifications,
            diagnostics=self.diagnostics
        )
        self.autopilot = AutopilotEngine(
            self.transmission,
            self.booster,
            self.decision_engine,
            vpn_adapter,
            self.storage,
            self.ollama,
            self.decision_engine.notifications
        )

        self.vpn_adapter = vpn_adapter
        self.running = False

    async def run(self):
        await self.storage.initialize()
        active_model = await self.ollama.get_active_model()
        self.running = True

        logger.info("=======================================================")
        logger.info("  🚀 Torrent Sentinel Daemon Starting Up")
        logger.info("=======================================================")
        logger.info("Transmission Host    : %s:%d (path: %s)", settings.TRANSMISSION_HOST, settings.TRANSMISSION_PORT, settings.TRANSMISSION_RPC_PATH)
        logger.info("Ollama Diagnostics   : enabled=%s (endpoint: %s, model: %s)", settings.OLLAMA_ENABLED, settings.OLLAMA_BASE_URL, active_model)
        logger.info("VPN Adapter Type     : %s (interface: %s)", settings.VPN_TYPE, settings.VPN_INTERFACE)
        logger.info("VPN Configs Dir      : %s", settings.VPN_CONFIGS_DIR)
        logger.info("Stalled Thresholds   : min_seeds=%d, min_rate=%.1f KB/s, stalled_duration=%d min", settings.MIN_SEEDS, settings.MIN_DOWNLOAD_RATE_KBPS, settings.STALLED_DURATION_MINUTES)
        logger.info("Anti-Flapping Rules  : cooldown=%d min, hourly_cap=%d, post_grace=%d min", settings.ROTATION_COOLDOWN_MINUTES, settings.MAX_ROTATIONS_PER_HOUR, settings.POST_ROTATION_GRACE_PERIOD_MINUTES)
        logger.info("Torrent Booster      : enabled=%s (grace=%dm, cadence=%dm, auto_failover=%s)", settings.BOOST_ENABLED, settings.RESCUE_GRACE_PERIOD_MINUTES, settings.AUTO_BOOST_CADENCE_MINUTES, settings.AUTO_FAILOVER_ENABLED)
        logger.info("Auto VPN Rotation    : enabled=%s", settings.AUTO_VPN_ROTATION_ENABLED)
        logger.info("Logging Level        : %s", settings.LOG_LEVEL)
        logger.info("=======================================================")

        # Run initial test connections and preload trackers
        if settings.OLLAMA_ENABLED:
            await self.ollama.health_check()

        if settings.BOOST_ENABLED:
            logger.info("Preloading verified public trackers in background...")
            asyncio.create_task(self.tracker_service.refresh_trackers())

        available_vpns = await self.vpn_adapter.get_available_locations()
        logger.info("Discovered %d initial VPN location profile(s).", len(available_vpns))

        # Check startup VPN status and Solo Off-VPN state
        vpn_enabled = await self.storage.get_vpn_enabled()
        off_vpn_state = await self.storage.get_off_vpn_state()

        if off_vpn_state and off_vpn_state.is_active:
            logger.warning(
                "Active Solo Off-VPN state detected on startup for torrent '%s'. Keeping VPN disconnected.",
                off_vpn_state.torrent_name or off_vpn_state.torrent_id
            )
            await self.vpn_adapter.disconnect()
        elif not vpn_enabled:
            logger.info("VPN is configured as DISABLED in settings. Direct internet routing active (no VPN).")
            await self.vpn_adapter.disconnect()
        else:
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
                try:
                    torrents = await self.transmission.get_torrents()
                except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
                    logger.warning(
                        "Cycle #%d: Transmission unreachable at %s (%s). Waiting for Transmission to start or verify connection settings...",
                        cycle, self.transmission.base_url, type(conn_err).__name__
                    )
                    await asyncio.sleep(15)
                    continue

                # 2. Check and enforce Solo Off-VPN lifecycle
                solo_off_vpn_active = await self.check_off_vpn_lifecycle(torrents, cycle=cycle)
                off_vpn_state = await self.storage.get_off_vpn_state()

                current_profile = await self.vpn_adapter.get_current_profile()
                if solo_off_vpn_active:
                    current_loc_name = f"Solo Off-VPN Direct ({off_vpn_state.torrent_name or off_vpn_state.torrent_id})"
                elif not (await self.is_vpn_enabled()):
                    current_loc_name = "Direct / VPN Disabled"
                else:
                    current_loc_name = current_profile.name if current_profile else "Disconnected / Direct"
                
                logger.info(
                    "Cycle #%d Status: %d active torrent(s) found | Current VPN: '%s'",
                    cycle, len(torrents), current_loc_name
                )

                # 3. Check periodic tracker refresh
                if settings.BOOST_ENABLED and self.tracker_service.should_refresh(settings.TRACKER_REFRESH_INTERVAL_HOURS):
                    logger.info("Cycle #%d: Tracker refresh interval reached (%dh). Refreshing and probing in background...", cycle, settings.TRACKER_REFRESH_INTERVAL_HOURS)
                    asyncio.create_task(self.tracker_service.refresh_trackers())

                # 4. Boost stuck torrents
                if settings.BOOST_ENABLED:
                    await self.booster.run_cycle(torrents)

                # 5. AI Autopilot Fleet Orchestration (if not OFF)
                autopilot_mode = await self.autopilot.get_mode()
                if autopilot_mode != AutopilotMode.OFF:
                    try:
                        logger.debug("Cycle #%d: Running AI Autopilot cycle (mode: %s)...", cycle, autopilot_mode.value)
                        await self.autopilot.run_autopilot_cycle(torrents)
                    except Exception as ap_err:
                        logger.error("AI Autopilot cycle encountered error: %s", ap_err, exc_info=True)

                # 6. Classic Fallback: LLM-Evaluated VPN Rotation (if Autopilot is OFF and VPN is active)
                elif await self.is_vpn_rotation_enabled() and (await self.is_vpn_enabled()) and not solo_off_vpn_active:
                    target_profile = await self.decision_engine.decide_rotation(
                        torrents,
                        stalled_records=self.booster.stalled_records
                    )

                    if target_profile:
                        reason = f"Automated rotation triggered on cycle #{cycle}"
                        logger.warning("Triggering rotation to '%s': %s", target_profile.name, reason)
                        success = await self.decision_engine.execute_rotation(current_profile, target_profile, reason)
                        if success:
                            logger.info("Cycle #%d: Successfully rotated to '%s'", cycle, target_profile.name)
                        else:
                            logger.error("Cycle #%d: Rotation to '%s' failed!", cycle, target_profile.name)
                    else:
                        logger.info("Cycle #%d: Swarm healthy or rotation not indicated. Standby.", cycle)
                else:
                    logger.debug("Cycle #%d: Auto VPN rotation is paused, disabled, or in Solo Off-VPN mode. Standby.", cycle)

                # 7. Wait for next interval
                logger.debug("Cycle #%d complete. Sleeping for 60 seconds...", cycle)
                await asyncio.sleep(60)

            except Exception as e:
                logger.error("Exception occurred in daemon cycle #%d: %s", cycle, e, exc_info=True)
                logger.info("Pausing 30 seconds before retrying daemon cycle...")
                await asyncio.sleep(30)

    async def is_vpn_enabled(self) -> bool:
        return await self.storage.get_vpn_enabled()

    async def disable_vpn(self) -> dict:
        """Disables the VPN connection and runs directly without VPN."""
        await self.storage.set_vpn_enabled(False)
        disconnected = await self.vpn_adapter.disconnect()
        logger.warning("VPN Gateway disabled by user. Swarm traffic routing directly without VPN.")
        await self.decision_engine.notifications.dispatch_alert(
            "🛡️ VPN Gateway Disabled",
            "Torrent Sentinel VPN has been disabled. Direct network connection active.",
            color=16744192
        )
        return {
            "status": "success",
            "message": "VPN disabled. Running without VPN.",
            "vpn_enabled": False,
            "connected": False
        }

    async def enable_vpn(self, preferred_location: Optional[str] = None) -> dict:
        """Enables and establishes the VPN connection."""
        await self.storage.set_vpn_enabled(True)
        available = await self.vpn_adapter.get_available_locations()
        target = None
        if preferred_location and available:
            pref = preferred_location.lower().strip()
            target = next((p for p in available if p.id.lower() == pref or p.name.lower() == pref), None)
        if not target and available:
            top_locations = await self.storage.get_top_locations(limit=1)
            if top_locations:
                target = next((p for p in available if p.id == top_locations[0]), None)
            if not target:
                target = available[0]

        if target:
            success = await self.vpn_adapter.rotate_to(target)
            msg = f"VPN gateway enabled. Connected to '{target.name}'."
            logger.info(msg)
            await self.decision_engine.notifications.dispatch_alert(
                "🛡️ VPN Gateway Enabled",
                msg,
                color=3066993
            )
            return {
                "status": "success",
                "message": msg,
                "vpn_enabled": True,
                "connected": success,
                "location": target.name
            }
        else:
            logger.warning("VPN enabled but no location profiles found in %s", settings.VPN_CONFIGS_DIR)
            return {
                "status": "warning",
                "message": "VPN enabled but no location profiles found.",
                "vpn_enabled": True,
                "connected": False,
                "location": None
            }

    async def check_off_vpn_lifecycle(self, torrents: List[TorrentInfo], cycle: int = 1) -> bool:
        """Enforces the Solo Off-VPN lifecycle for the active torrent queue.
        Returns True if Solo Off-VPN mode is active, False otherwise."""
        off_vpn_state = await self.storage.get_off_vpn_state()
        if not (off_vpn_state and off_vpn_state.is_active):
            return False

        target_id = off_vpn_state.torrent_id
        target_hash = off_vpn_state.torrent_hash
        target_t = next((t for t in torrents if t.id == target_id or (target_hash and t.hash.lower() == target_hash.lower())), None)

        if not target_t:
            logger.warning(
                "Cycle #%d: Solo Off-VPN target torrent (ID: %s) no longer exists in Transmission. Auto-restoring VPN and queue...",
                cycle, target_id
            )
            await self.restore_from_solo_off_vpn()
            return False

        is_complete = target_t.progress >= 1.0 or str(target_t.status).lower() in ("seed", "seeding", "6")
        if is_complete and off_vpn_state.auto_restore_on_complete:
            logger.info(
                "Cycle #%d: Solo Off-VPN torrent '%s' reached 100%% completion! Auto-restoring VPN and fleet queue...",
                cycle, target_t.name
            )
            # Stop target to prevent uploading off-VPN
            await self.transmission.pause(target_t.id)
            await self.restore_from_solo_off_vpn()
            return False

        # Enforce: Ensure target is running
        if str(target_t.status) in ("0", "stopped", "paused"):
            await self.transmission.resume(target_t.id)

        # Enforce: Ensure all other torrents remain paused
        unwanted_active = [t.id for t in torrents if t.id != target_t.id and str(t.status) not in ("0", "stopped", "paused")]
        if unwanted_active:
            logger.info(
                "Cycle #%d: Enforcing Solo Off-VPN isolation: pausing %d non-target active torrent(s)...",
                cycle, len(unwanted_active)
            )
            await self.transmission.pause_torrents(unwanted_active)

        # Enforce: Ensure VPN remains disconnected
        if await self.vpn_adapter.is_connected():
            await self.vpn_adapter.disconnect()

        return True

    async def start_solo_off_vpn(self, torrent_identifier: str, auto_restore: bool = True) -> dict:
        """Pauses all other torrents, turns off the VPN, and runs only the selected torrent off-VPN."""
        torrents = await self.transmission.get_torrents()
        target = next((t for t in torrents if t.id == torrent_identifier or t.hash.lower() == torrent_identifier.lower()), None)
        if not target:
            return {"status": "error", "message": f"Torrent '{torrent_identifier}' not found in Transmission."}

        cur_prof = await self.vpn_adapter.get_current_profile()
        prev_loc = cur_prof.id if cur_prof else None

        # Identify running torrents to pause
        running_other_ids = [t.id for t in torrents if t.id != target.id and str(t.status) not in ("0", "stopped", "paused")]
        all_other_ids = [t.id for t in torrents if t.id != target.id]

        # 1. Pause other torrents
        if all_other_ids:
            logger.info("Solo Off-VPN: Pausing %d other torrents in Transmission...", len(all_other_ids))
            await self.transmission.pause_torrents(all_other_ids)

        # 2. Resume target torrent
        logger.info("Solo Off-VPN: Starting target torrent '%s' (ID: %s)...", target.name, target.id)
        await self.transmission.resume(target.id)

        # 3. Disconnect VPN
        logger.info("Solo Off-VPN: Disconnecting VPN gateway tunnel...")
        await self.vpn_adapter.disconnect()

        # 4. Save state
        state = OffVpnState(
            is_active=True,
            torrent_id=target.id,
            torrent_hash=target.hash,
            torrent_name=target.name,
            paused_torrent_ids=running_other_ids,
            previous_vpn_location=prev_loc,
            started_at=datetime.now(timezone.utc),
            auto_restore_on_complete=auto_restore
        )
        await self.storage.set_off_vpn_state(state)

        msg = f"Solo Off-VPN mode started for '{target.name}'. Paused {len(running_other_ids)} other active torrent(s) and disabled VPN tunnel."
        logger.warning(msg)
        await self.booster.add_history(
            torrent_id=target.id,
            torrent_hash=target.hash,
            torrent_name=target.name,
            action="solo_off_vpn_started",
            details=f"Paused {len(running_other_ids)} torrent(s); VPN disconnected"
        )
        await self.decision_engine.notifications.dispatch_alert(
            "⚠️ Solo Off-VPN Mode Started",
            msg,
            color=15105570
        )
        return {
            "status": "success",
            "message": msg,
            "torrent_id": target.id,
            "torrent_name": target.name,
            "paused_count": len(running_other_ids)
        }

    async def restore_from_solo_off_vpn(self) -> dict:
        """Restores the VPN gateway and resumes the previously paused torrents."""
        state = await self.storage.get_off_vpn_state()
        if not state or not state.is_active:
            return {"status": "noop", "message": "Solo Off-VPN mode is not currently active."}

        # 1. Restore VPN
        await self.storage.set_vpn_enabled(True)
        available = await self.vpn_adapter.get_available_locations()
        target_profile = None
        if state.previous_vpn_location and available:
            target_profile = next((p for p in available if p.id == state.previous_vpn_location), None)
        if not target_profile and available:
            top_locations = await self.storage.get_top_locations(limit=1)
            if top_locations:
                target_profile = next((p for p in available if p.id == top_locations[0]), None)
            if not target_profile:
                target_profile = available[0]

        if target_profile:
            logger.info("Solo Off-VPN Restore: Re-establishing VPN tunnel to '%s'...", target_profile.name)
            await self.vpn_adapter.rotate_to(target_profile)

        # 2. Resume paused torrents
        if state.paused_torrent_ids:
            logger.info("Solo Off-VPN Restore: Resuming %d previously paused torrent(s)...", len(state.paused_torrent_ids))
            await self.transmission.resume_torrents(state.paused_torrent_ids)

        # 3. Clear off-vpn state
        await self.storage.set_off_vpn_state(None)

        msg = f"Restored VPN gateway and resumed {len(state.paused_torrent_ids)} previously paused torrent(s)."
        logger.info(msg)
        await self.booster.add_history(
            torrent_id=state.torrent_id or "",
            torrent_hash=state.torrent_hash or "",
            torrent_name=state.torrent_name or "",
            action="solo_off_vpn_restored",
            details=f"VPN re-established; resumed {len(state.paused_torrent_ids)} torrents"
        )
        await self.decision_engine.notifications.dispatch_alert(
            "🛡️ VPN Restored & Queue Resumed",
            msg,
            color=3066993
        )
        return {
            "status": "success",
            "message": msg,
            "resumed_count": len(state.paused_torrent_ids),
            "vpn_location": target_profile.name if target_profile else "Direct"
        }

    async def get_off_vpn_state(self) -> Optional[OffVpnState]:
        return await self.storage.get_off_vpn_state()

    async def is_vpn_rotation_enabled(self) -> bool:
        val = await self.storage.get_setting("auto_vpn_rotation_enabled")
        if val is not None:
            return val.lower() == "true"
        return settings.AUTO_VPN_ROTATION_ENABLED

    async def set_vpn_rotation_enabled(self, enabled: bool):
        await self.storage.set_setting("auto_vpn_rotation_enabled", str(enabled).lower())

    async def is_vpn_rotation_paused(self) -> bool:
        return not (await self.is_vpn_rotation_enabled())

    async def set_vpn_rotation_paused(self, paused: bool):
        await self.set_vpn_rotation_enabled(not paused)

    def stop(self):
        logger.info("Stopping Torrent Sentinel Daemon...")
        self.running = False
