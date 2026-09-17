import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

from torrent_sentinel.config import settings
from torrent_sentinel.models import RotationEvent, LocationProfile, TorrentInfo
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ip_verifier import IPVerifier
from torrent_sentinel.engine.diagnostics import Diagnostics
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.vpn.base import BaseVPNAdapter
from torrent_sentinel.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)

class DecisionEngine:
    def __init__(self, diagnostics: Diagnostics, storage: Storage, vpn_adapter: BaseVPNAdapter):
        self.diagnostics = diagnostics
        self.storage = storage
        self.vpn_adapter = vpn_adapter
        self.transmission = TransmissionClient()
        self.ip_verifier = IPVerifier()
        self.notifications = NotificationDispatcher()

    async def _check_anti_flapping(self) -> bool:
        """Returns True if rotation is allowed, False if suppressed by anti-flapping rules."""
        history = await self.storage.get_history()
        now = datetime.now()

        if not history:
            logger.debug("Anti-flapping: No previous rotation events found. Rotation permitted.")
            return True

        # 1. Check Cooldown
        last_event = history[0]
        cooldown_seconds = settings.ROTATION_COOLDOWN_MINUTES * 60
        elapsed_seconds = (now - last_event.timestamp).total_seconds()

        if elapsed_seconds < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed_seconds)
            logger.warning(
                "Anti-Flapping Guard: Cooldown active! Last rotation was %d seconds ago to '%s'. Cooldown requires %d min (%d seconds remaining). Suppressing rotation.",
                int(elapsed_seconds), last_event.to_location, settings.ROTATION_COOLDOWN_MINUTES, remaining
            )
            return False

        # 2. Check Hourly Limit
        one_hour_ago = now - timedelta(hours=1)
        rotations_past_hour = sum(1 for e in history if e.timestamp >= one_hour_ago)

        if rotations_past_hour >= settings.MAX_ROTATIONS_PER_HOUR:
            logger.warning(
                "Anti-Flapping Guard: Rate limit reached! %d rotation(s) occurred in the last hour (max allowed: %d). Suppressing rotation.",
                rotations_past_hour, settings.MAX_ROTATIONS_PER_HOUR
            )
            return False

        logger.debug(
            "Anti-flapping check passed (elapsed: %ds, rotations in past hour: %d/%d).",
            int(elapsed_seconds), rotations_past_hour, settings.MAX_ROTATIONS_PER_HOUR
        )
        return True

    async def decide_rotation(
        self,
        current_torrents: List[TorrentInfo],
        stalled_records: Optional[dict] = None
    ) -> Optional[LocationProfile]:
        logger.debug("DecisionEngine: Checking if rotation is needed for %d torrent(s)...", len(current_torrents))
        diagnosis = await self.diagnostics.analyze_torrents(current_torrents, stalled_records=stalled_records)

        if not diagnosis:
            logger.debug("DecisionEngine: No diagnosis returned; torrents are healthy.")
            return None

        if not diagnosis.should_rotate:
            logger.info("DecisionEngine: AI analyzed torrents and determined NO rotation is needed (Action: %s).", diagnosis.suggested_action)
            return None

        logger.info(
            "DecisionEngine: AI recommended rotating VPN! Confidence: %.2f | Recommended: '%s' | Action: '%s'",
            diagnosis.confidence, diagnosis.recommended_location or "Any", diagnosis.suggested_action
        )
        logger.info("DecisionEngine reasoning: %s", diagnosis.reasoning)

        # Evaluate anti-flapping guards
        if not await self._check_anti_flapping():
            return None

        # Determine target profile
        available = await self.vpn_adapter.get_available_locations()
        if not available:
            logger.error("DecisionEngine: No VPN profiles available in configured directory (%s)!", settings.VPN_CONFIGS_DIR)
            return None

        current_profile = await self.vpn_adapter.get_current_profile()
        current_id = current_profile.id if current_profile else None
        candidate_profiles = [p for p in available if p.id != current_id]

        if not candidate_profiles:
            logger.warning("DecisionEngine: Only one VPN profile exists or candidate matches current location. Cannot rotate.")
            return None

        target_profile = None

        # Try Ollama recommended profile if specified
        if diagnosis.recommended_location:
            rec = diagnosis.recommended_location.lower()
            target_profile = next((p for p in candidate_profiles if rec in p.id.lower() or rec in p.name.lower()), None)
            if target_profile:
                logger.info("Selected AI recommended profile: %s", target_profile.name)

        # Fallback to scoreboard top performers
        if not target_profile:
            top_locations = await self.storage.get_top_locations(limit=5)
            logger.debug("Scoreboard top locations: %s", top_locations)
            for top_id in top_locations:
                target_profile = next((p for p in candidate_profiles if p.id == top_id), None)
                if target_profile:
                    logger.info("Selected top-scoring profile from scoreboard: %s", target_profile.name)
                    break

        # Fallback to first available alternative
        if not target_profile:
            target_profile = candidate_profiles[0]
            logger.info("Selected next available profile: %s", target_profile.name)

        return target_profile

    async def execute_rotation(self, from_profile: Optional[LocationProfile], to_profile: LocationProfile, reason: str) -> bool:
        from_name = from_profile.name if from_profile else "Initial / Unknown"
        logger.info("================== INITIATING VPN ROTATION ==================")
        logger.info("Rotation Target: %s -> %s", from_name, to_profile.name)
        logger.info("Reason: %s", reason)

        # 1. Capture Initial State
        old_ip = None
        try:
            old_ip = await self.ip_verifier.get_public_ip()
            logger.info("Pre-rotation public IP: %s", old_ip)
        except Exception as e:
            logger.warning("Could not capture pre-rotation IP: %s", e)

        # Capture baseline peer counts
        initial_torrents = []
        try:
            initial_torrents = await self.transmission.get_torrents()
        except Exception as e:
            logger.warning("Could not fetch pre-rotation torrent metrics: %s", e)
        peers_before = sum(t.peers_connected for t in initial_torrents)
        logger.info("Pre-rotation total connected peers: %d", peers_before)

        # 2. Rotate VPN Interface
        logger.info("Step 1/4: Applying WireGuard configuration for '%s'...", to_profile.name)
        success = await self.vpn_adapter.rotate_to(to_profile)
        if not success:
            logger.error("Step 1/4 FAILED: WireGuard rotation command failed. Aborting rotation.")
            return False
        logger.info("Step 1/4 SUCCESS: WireGuard profile '%s' applied.", to_profile.name)

        # 3. Verify IP Change
        logger.info("Step 2/4: Waiting 5 seconds for network routes to stabilize...")
        await asyncio.sleep(5)

        ip_changed = False
        new_ip = None
        if old_ip:
            try:
                new_ip = await self.ip_verifier.get_public_ip()
                ip_changed = (new_ip != old_ip)
                if ip_changed:
                    logger.info("Step 2/4 SUCCESS: Public IP confirmed rotated: %s -> %s", old_ip, new_ip)
                else:
                    logger.warning("Step 2/4 WARNING: Public IP did not change! Still: %s", new_ip)
            except Exception as e:
                logger.warning("Step 2/4 WARNING: Public IP verification failed: %s", e)
        else:
            logger.info("Step 2/4: Skipping IP diff check (pre-rotation IP was unknown)")

        # 4. Re-announce Torrents
        logger.info("Step 3/4: Requesting tracker re-announce in Transmission...")
        torrent_ids = [t.id for t in initial_torrents] if initial_torrents else []
        if torrent_ids:
            try:
                await self.transmission.reannounce_torrents(torrent_ids)
                logger.info("Step 3/4 SUCCESS: Re-announce triggered for %d torrent(s).", len(torrent_ids))
            except Exception as e:
                logger.error("Step 3/4 ERROR: Re-announce failed: %s", e)
        else:
            logger.info("Step 3/4: No torrents to re-announce.")

        # 5. Record Event & Notifications
        logger.info("Step 4/4: Waiting %d minute(s) grace period to measure swarm peer recovery...", settings.POST_ROTATION_GRACE_PERIOD_MINUTES)
        peers_after = peers_before
        if settings.POST_ROTATION_GRACE_PERIOD_MINUTES > 0:
            await asyncio.sleep(settings.POST_ROTATION_GRACE_PERIOD_MINUTES * 60)
            try:
                post_torrents = await self.transmission.get_torrents()
                peers_after = sum(t.peers_connected for t in post_torrents)
                logger.info("Post-rotation peer count: %d (Delta: %+d peers)", peers_after, peers_after - peers_before)
            except Exception as e:
                logger.warning("Could not measure post-rotation peers: %s", e)

        event = RotationEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            from_location=from_name,
            to_location=to_profile.name,
            reason=reason,
            peers_before=peers_before,
            peers_after=peers_after
        )

        try:
            await self.storage.record_rotation(event)
            peer_delta = peers_after - peers_before
            await self.storage.update_score(to_profile.id, peer_delta)
            logger.info("Recorded rotation event %s in SQLite scoreboard.", event.id)
        except Exception as e:
            logger.error("Failed to record rotation event to database: %s", e)

        try:
            await self.notifications.dispatch_rotation(event)
            logger.info("Dispatched rotation notifications.")
        except Exception as e:
            logger.error("Notification dispatch failed: %s", e)

        logger.info("================== ROTATION COMPLETE ==================")
        return True
