import logging
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from torrent_sentinel.config import settings
from torrent_sentinel.models import (
    TorrentInfo,
    BoostState,
    ServarrType,
    ServarrQueueItem,
    UnifiedTorrentItem,
    BoostEvent,
    RecommendedAction,
    TorrentJudgement
)

from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.servarr import ServarrClient
from torrent_sentinel.services.tracker_service import TrackerService
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)

class StalledRecord:
    def __init__(self, torrent_id: str, torrent_hash: str):
        self.torrent_id = torrent_id
        self.torrent_hash = torrent_hash
        self.first_stalled_at: datetime = datetime.now(timezone.utc)
        self.boosted_at: Optional[datetime] = None
        self.grace_period_expires_at: Optional[datetime] = None
        self.state: BoostState = BoostState.STALLED
        self.servarr_app: Optional[ServarrType] = None
        self.servarr_queue_id: Optional[int] = None
        self.raw_servarr_record: Optional[Dict[str, Any]] = None
        self.status_message: str = "Stalled download detected"
        self.error_message: Optional[str] = None
        self.recheck_attempted: bool = False
        self.boost_count: int = 0

class TorrentBooster:
    def __init__(
        self,
        transmission: TransmissionClient,
        tracker_service: TrackerService,
        storage: Storage,
        notifications: Optional[NotificationDispatcher] = None,
        diagnostics: Optional[Any] = None
    ):
        self.transmission = transmission
        self.trackers = tracker_service
        self.storage = storage
        self.notifications = notifications or NotificationDispatcher()
        self.diagnostics = diagnostics

        self.servarr_clients: Dict[ServarrType, ServarrClient] = {}
        if settings.SONARR_URL and settings.SONARR_API_KEY:
            self.servarr_clients[ServarrType.SONARR] = ServarrClient(
                app_type=ServarrType.SONARR,
                base_url=settings.SONARR_URL,
                api_key=settings.SONARR_API_KEY
            )
        if settings.RADARR_URL and settings.RADARR_API_KEY:
            self.servarr_clients[ServarrType.RADARR] = ServarrClient(
                app_type=ServarrType.RADARR,
                base_url=settings.RADARR_URL,
                api_key=settings.RADARR_API_KEY
            )
        if settings.LIDARR_URL and settings.LIDARR_API_KEY:
            self.servarr_clients[ServarrType.LIDARR] = ServarrClient(
                app_type=ServarrType.LIDARR,
                base_url=settings.LIDARR_URL,
                api_key=settings.LIDARR_API_KEY
            )

        self.stalled_records: Dict[str, StalledRecord] = {}
        self.last_cadence_boost: Dict[str, datetime] = {}

    async def is_llm_assisted_failover_enabled(self) -> bool:
        val = await self.storage.get_setting("llm_assisted_failover_enabled")
        if val is not None:
            return val.lower() in ("true", "1", "yes")
        return settings.LLM_ASSISTED_FAILOVER

    async def set_llm_assisted_failover_enabled(self, enabled: bool):
        await self.storage.set_setting("llm_assisted_failover_enabled", str(enabled).lower())


    async def is_auto_failover_enabled(self) -> bool:
        """Check dynamic setting in database, fallback to settings.py."""
        val = await self.storage.get_setting("auto_failover_enabled")
        if val is not None:
            return val.lower() in ("true", "1", "yes")
        return settings.AUTO_FAILOVER_ENABLED

    async def set_auto_failover_enabled(self, enabled: bool):
        await self.storage.set_setting("auto_failover_enabled", str(enabled).lower())

    async def add_history(
        self,
        torrent_id: str,
        torrent_hash: str,
        torrent_name: str,
        action: str,
        details: str,
        servarr_app: Optional[ServarrType] = None,
        success: bool = True
    ):
        event = BoostEvent(
            id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(timezone.utc),
            torrent_id=torrent_id,
            torrent_hash=torrent_hash,
            torrent_name=torrent_name,
            action=action,
            details=details,
            servarr_app=servarr_app,
            success=success
        )
        try:
            await self.storage.record_boost_event(event)
        except Exception as e:
            logger.error("Failed to store boost event: %s", e)

    async def get_servarr_queues(self) -> Dict[str, Tuple[ServarrQueueItem, Dict[str, Any]]]:
        """Fetch and aggregate queues from all configured Servarr apps indexed by lowercase downloadId."""
        servarr_queue_by_hash: Dict[str, Tuple[ServarrQueueItem, Dict[str, Any]]] = {}
        for app_type, client in self.servarr_clients.items():
            try:
                items = await client.get_queue()
                for q_item, raw in items:
                    if q_item.download_id:
                        servarr_queue_by_hash[q_item.download_id.lower()] = (q_item, raw)
            except Exception as e:
                logger.error("Error fetching queue from %s: %s", app_type, e)
        return servarr_queue_by_hash

    async def get_unified_queue(self, torrents: Optional[List[TorrentInfo]] = None) -> List[UnifiedTorrentItem]:
        """Compile a unified list of torrents with boost state and Servarr metadata."""
        if torrents is None:
            torrents = await self.transmission.get_torrents()

        servarr_queue = await self.get_servarr_queues()
        latest_judgements = {}
        try:
            latest_judgements = await self.storage.get_all_latest_judgements()
        except Exception as e:
            logger.debug("Could not fetch latest judgements: %s", e)

        unified_list: List[UnifiedTorrentItem] = []

        for t in torrents:
            t_key = t.hash.lower() if t.hash else t.id
            servarr_match = servarr_queue.get(t.hash.lower()) if t.hash else None

            servarr_app = servarr_match[0].app if servarr_match else None
            servarr_title = (
                servarr_match[0].series_title or
                servarr_match[0].movie_title or
                servarr_match[0].album_title or
                servarr_match[0].title
            ) if servarr_match else None
            servarr_queue_id = servarr_match[0].id if servarr_match else None

            rec = self.stalled_records.get(t_key)

            client_is_errored = (t.error is not None and t.error != 0 and str(t.error) != "0") or bool(t.error_string)
            is_complete = t.progress >= 1.0 or t.status.lower() in ("seed", "seeding", "uploading")

            if client_is_errored:
                state = BoostState.ERROR
                status_msg = f"Client Error: {t.error_string or t.error}"
            elif is_complete:
                state = BoostState.COMPLETED
                status_msg = "Download 100% complete"
            elif rec:
                state = rec.state
                status_msg = rec.status_message
            else:
                state = BoostState.HEALTHY
                status_msg = "Operating normally"

            judgement = latest_judgements.get(t.hash.lower()) if t.hash else None
            if not judgement and t.id:
                judgement = latest_judgements.get(t.id)

            unified_list.append(UnifiedTorrentItem(
                id=t.id,
                hash=t.hash,
                name=t.name,
                status=t.status,
                progress=round(t.progress, 4),
                rate_download=t.rate_download,
                rate_upload=t.rate_upload,
                peers_connected=t.peers_connected,
                peers_sending_to_us=t.peers_sending_to_us,
                num_seeds=t.peers_sending_to_us,
                num_leechs=max(t.peers_connected - t.peers_sending_to_us, 0),
                eta=t.eta,
                error=t.error,
                error_string=t.error_string,
                boost_state=state,
                status_message=status_msg,
                first_stalled_at=rec.first_stalled_at if rec else None,
                boosted_at=rec.boosted_at if rec else None,
                grace_period_expires_at=rec.grace_period_expires_at if rec else None,
                servarr_app=servarr_app,
                servarr_title=servarr_title,
                servarr_queue_id=servarr_queue_id,
                is_errored=client_is_errored,
                is_private=getattr(t, "is_private", False),
                latest_judgement=judgement
            ))


        return unified_list

    async def run_cycle(self, torrents: List[TorrentInfo]):
        """Run inspection, tracker injection, auto-repair, and revival checks on all torrents."""
        if not torrents:
            return

        now = datetime.now(timezone.utc)
        servarr_queue = await self.get_servarr_queues()
        active_keys = set()

        min_rate_bytes = settings.MIN_DOWNLOAD_RATE_KBPS * 1024.0

        # Record Transmission trackerStats into TrackerService for real-world health auditing
        for t in torrents:
            if getattr(t, "tracker_stats", None):
                self.trackers.record_transmission_stats(t.tracker_stats)

        for t in torrents:
            t_key = t.hash.lower() if t.hash else t.id
            active_keys.add(t_key)

            servarr_match = servarr_queue.get(t.hash.lower()) if t.hash else None
            servarr_app = servarr_match[0].app if servarr_match else None
            servarr_queue_id = servarr_match[0].id if servarr_match else None
            raw_record = servarr_match[1] if servarr_match else None

            # 1. Error detection and auto-repair
            client_is_errored = (t.error is not None and t.error != 0 and str(t.error) != "0") or bool(t.error_string)
            if client_is_errored:
                rec = self.stalled_records.get(t_key)
                if not rec:
                    rec = StalledRecord(t.id, t.hash)
                    rec.servarr_app = servarr_app
                    rec.servarr_queue_id = servarr_queue_id
                    rec.raw_servarr_record = raw_record
                    self.stalled_records[t_key] = rec

                rec.state = BoostState.ERROR
                rec.error_message = t.error_string or str(t.error)
                rec.status_message = f"Error: {rec.error_message}"

                if not rec.recheck_attempted:
                    rec.recheck_attempted = True
                    logger.info("[Auto-Repair] Torrent '%s' has error (%s). Attempting force recheck and resume...", t.name, rec.error_message)
                    await self.transmission.recheck(t.id)
                    await self.transmission.resume(t.id)
                    await self.add_history(
                        t.id, t.hash, t.name, "auto_recheck",
                        f"Auto-repair: Force integrity recheck & resume dispatched for error: {rec.error_message}",
                        servarr_app=servarr_app
                    )
                continue

            # 2. Check if completed
            is_complete = t.progress >= 1.0 or t.status.lower() in ("seed", "seeding", "uploading")
            if is_complete:
                if t_key in self.stalled_records:
                    logger.info("Torrent '%s' is complete. Removing from stalled tracking.", t.name)
                    del self.stalled_records[t_key]
                if t_key in self.last_cadence_boost:
                    del self.last_cadence_boost[t_key]
                continue

            # 3. Check Cadence Auto-Boost
            if not getattr(t, "is_private", False) and settings.AUTO_BOOST_CADENCE_MINUTES > 0 and t.progress < 1.0:
                last_boost = self.last_cadence_boost.get(t_key)
                if last_boost is None:
                    self.last_cadence_boost[t_key] = now
                elif (now - last_boost) >= timedelta(minutes=settings.AUTO_BOOST_CADENCE_MINUTES):
                    trackers = self.trackers.get_trackers()
                    logger.info("[Cadence Boost] Injecting %d trackers into '%s' (cadence: %dm)", len(trackers), t.name, settings.AUTO_BOOST_CADENCE_MINUTES)
                    await self.transmission.add_trackers(t.id, trackers)
                    await self.transmission.reannounce_torrents([t.id])
                    self.last_cadence_boost[t_key] = now
                    await self.add_history(
                        t.id, t.hash, t.name, "cadence_boost",
                        f"Periodic auto-boost: Injected {len(trackers)} trackers and re-announced (cadence: {settings.AUTO_BOOST_CADENCE_MINUTES}m)",
                        servarr_app=servarr_app
                    )

            # 4. Check if downloading healthily (or recovered)
            is_healthy = (
                t.rate_download >= min_rate_bytes and
                t.peers_connected >= settings.MIN_SEEDS
            )

            if is_healthy:
                if t_key in self.stalled_records:
                    rec = self.stalled_records[t_key]
                    if rec.state == BoostState.BOOSTING:
                        dl_kbps = t.rate_download / 1024.0
                        logger.info("🎉 Swarm REVIVED for '%s'! Speed: %.1f KB/s with %d peers.", t.name, dl_kbps, t.peers_connected)
                        await self.add_history(
                            t.id, t.hash, t.name, "swarm_revived",
                            f"Torrent revived after boosting! Downloading at {dl_kbps:.1f} KB/s with {t.peers_connected} peers.",
                            servarr_app=servarr_app
                        )
                        try:
                            await self.notifications.send_swarm_recovery(t.name, t.peers_connected, "Swarm Boost")
                        except Exception as ne:
                            logger.error("Failed to dispatch swarm revival notification: %s", ne)
                    del self.stalled_records[t_key]
                continue

            # 5. Check if stalled
            is_stalled = (
                t.progress < 1.0 and
                (
                    t.peers_connected < settings.MIN_SEEDS or
                    t.rate_download < min_rate_bytes or
                    t.status.lower() in ("stalleddl", "metadl", "allocating")
                )
            )

            if not is_stalled:
                continue

            # Handle stalled state
            if t_key not in self.stalled_records:
                rec = StalledRecord(t.id, t.hash)
                rec.servarr_app = servarr_app
                rec.servarr_queue_id = servarr_queue_id
                rec.raw_servarr_record = raw_record
                rec.status_message = f"Stalled detected (peers: {t.peers_connected}, dl: {t.rate_download/1024:.1f} KB/s)"
                self.stalled_records[t_key] = rec
                logger.info("Tracking stalled torrent '%s' (#%s, hash: %s)", t.name, t.id, t.hash[:8] if t.hash else "")
            else:
                rec = self.stalled_records[t_key]
                if servarr_app:
                    rec.servarr_app = servarr_app
                    rec.servarr_queue_id = servarr_queue_id
                    rec.raw_servarr_record = raw_record

            stalled_duration = now - rec.first_stalled_at

            # Stage 1: Swarm Booster (Tracker Injection)
            if rec.state == BoostState.STALLED:
                if stalled_duration >= timedelta(minutes=settings.STALL_THRESHOLD_MINUTES):
                    await self._execute_stage_1_boost(t, rec)

            # Stage 2: Probation Expiry & Failover
            elif rec.state == BoostState.BOOSTING:
                if rec.grace_period_expires_at and now >= rec.grace_period_expires_at:
                    rec.state = BoostState.PROBATION_EXPIRED
                    rec.status_message = "Rescue grace period expired. Seeds not found."
                    logger.warning("Torrent '%s' probation grace period expired without seeds.", t.name)

                    auto_failover = await self.is_auto_failover_enabled()
                    llm_assisted = await self.is_llm_assisted_failover_enabled()

                    # Consult LLM Judge prior to taking destructive failover action
                    ai_postponed = False
                    if auto_failover and llm_assisted and self.diagnostics:
                        try:
                            logger.info("[AI Failover Check] Consulting LLM before deciding failover for '%s'...", t.name)
                            judgement = await self.diagnostics.judge_torrent(t, stalled_rec=rec, servarr_match=servarr_match)
                            if judgement:
                                await self.storage.record_judgement(judgement)
                                if judgement.recommended_action in (RecommendedAction.WAIT, RecommendedAction.RECHECK, RecommendedAction.REANNOUNCE):
                                    ai_postponed = True
                                    rec.state = BoostState.BOOSTING
                                    rec.grace_period_expires_at = now + timedelta(minutes=settings.RESCUE_GRACE_PERIOD_MINUTES)
                                    rec.status_message = f"AI advised {judgement.recommended_action.value} ({judgement.verdict.value}): {judgement.action_explanation}"
                                    logger.info(
                                        "AI Judge advised %s for '%s' (verdict: %s, viability: %.0f%%). Deferring failover by %dm.",
                                        judgement.recommended_action.value, t.name, judgement.verdict.value,
                                        judgement.viability_score * 100, settings.RESCUE_GRACE_PERIOD_MINUTES
                                    )
                                    await self.add_history(
                                        t.id, t.hash, t.name, "ai_postpone_failover",
                                        f"AI evaluated release as {judgement.verdict.value} (viability {judgement.viability_score*100:.0f}%). Advised '{judgement.recommended_action.value}'. Extended probation by {settings.RESCUE_GRACE_PERIOD_MINUTES}m.",
                                        servarr_app=rec.servarr_app
                                    )
                                    if judgement.recommended_action == RecommendedAction.RECHECK:
                                        await self.transmission.recheck(t.id)
                                        await self.transmission.resume(t.id)
                                    elif judgement.recommended_action == RecommendedAction.REANNOUNCE:
                                        await self.transmission.reannounce_torrents([t.id])
                        except Exception as ai_err:
                            logger.warning("Could not obtain AI judgement prior to failover: %s", ai_err)

                    if not ai_postponed:
                        if auto_failover:
                            logger.info("Auto-failover enabled; executing failover for '%s'...", t.name)
                            await self._execute_stage_2_failover(t, rec)
                        else:
                            logger.info("Auto-failover is disabled; leaving '%s' in probation_expired for manual action.", t.name)


        # Cleanup stale records
        for stale_key in list(self.stalled_records.keys()):
            if stale_key not in active_keys:
                del self.stalled_records[stale_key]
        for stale_key in list(self.last_cadence_boost.keys()):
            if stale_key not in active_keys:
                del self.last_cadence_boost[stale_key]

    async def _execute_stage_1_boost(self, t: TorrentInfo, rec: StalledRecord):
        """Inject verified public trackers and force re-announce."""
        if getattr(t, "is_private", False):
            logger.info("[Stage 1 Boost] Skipping boost for '%s' (#%s): marked PRIVATE.", t.name, t.id)
            rec.status_message = "Private torrent; public tracker injection skipped."
            return

        trackers = self.trackers.get_trackers()
        logger.info("[Stage 1 Boost] Injecting %d verified trackers into '%s' (#%s)...", len(trackers), t.name, t.id)

        added = await self.transmission.add_trackers(t.id, trackers)
        await self.transmission.reannounce_torrents([t.id])
        await self.transmission.add_labels(t.id, ["sentinel-boosted"])

        now = datetime.now(timezone.utc)
        rec.boosted_at = now
        rec.boost_count += 1
        self.last_cadence_boost[t.hash.lower() if t.hash else t.id] = now
        rec.grace_period_expires_at = now + timedelta(minutes=settings.RESCUE_GRACE_PERIOD_MINUTES)
        rec.state = BoostState.BOOSTING
        rec.status_message = f"Trackers injected. In probation until {rec.grace_period_expires_at.strftime('%H:%M:%S UTC')}."

        await self.add_history(
            t.id, t.hash, t.name, "trackers_injected",
            f"Injected {len(trackers)} public trackers & re-announced. Probation grace period: {settings.RESCUE_GRACE_PERIOD_MINUTES}m.",
            servarr_app=rec.servarr_app
        )

    async def _execute_stage_2_failover(self, t: TorrentInfo, rec: StalledRecord) -> bool:
        """Blocklist dead release in Servarr, trigger replacement search, and remove download."""
        logger.info("[Stage 2 Failover] Executing failover for dead torrent '%s'...", t.name)

        if rec.servarr_app and rec.servarr_queue_id and rec.servarr_app in self.servarr_clients:
            client = self.servarr_clients[rec.servarr_app]

            # 1. Trigger replacement search first
            search_ok, cmd_name = False, None
            if rec.raw_servarr_record:
                search_ok, cmd_name = await client.trigger_search_for_record(rec.raw_servarr_record)

            # 2. Blocklist and remove from Servarr
            delete_ok = await client.remove_and_blocklist(rec.servarr_queue_id)

            rec.state = BoostState.FAILED_OVER
            rec.status_message = f"Failed over: Release blocklisted, {cmd_name or 'Search'} dispatched in {rec.servarr_app.value}."

            await self.add_history(
                t.id, t.hash, t.name, "failover_executed",
                f"Release blocklisted in {rec.servarr_app.value} and triggered {cmd_name or 'replacement search'}. Dead torrent removed.",
                servarr_app=rec.servarr_app,
                success=(delete_ok and search_ok)
            )
            return True
        else:
            del_ok = await self.transmission.delete_torrent(t.id, delete_files=True)
            rec.state = BoostState.FAILED_OVER
            rec.status_message = "Removed dead torrent from Transmission (not linked to Servarr queue)."
            await self.add_history(
                t.id, t.hash, t.name, "torrent_deleted",
                "Removed dead torrent from Transmission (not linked to active Servarr queue).",
                success=del_ok
            )
            return del_ok

    async def manual_boost(self, identifier: str) -> bool:
        """Manually trigger immediate tracker injection and re-announce."""
        torrents = await self.transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            return False

        if getattr(target, "is_private", False):
            logger.warning("Manual boost aborted: Torrent '%s' (#%s) is marked PRIVATE.", target.name, target.id)
            return False

        t_key = target.hash.lower() if target.hash else target.id
        rec = self.stalled_records.get(t_key)
        if not rec:
            rec = StalledRecord(target.id, target.hash)
            self.stalled_records[t_key] = rec

        await self._execute_stage_1_boost(target, rec)
        return True

    async def manual_recheck(self, identifier: str) -> bool:
        """Manually trigger force verification & resume."""
        torrents = await self.transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            return False

        recheck_ok = await self.transmission.recheck(target.id)
        resume_ok = await self.transmission.resume(target.id)

        t_key = target.hash.lower() if target.hash else target.id
        rec = self.stalled_records.get(t_key)
        if rec:
            rec.recheck_attempted = True
            rec.status_message = "Manual integrity recheck and resume command sent."

        await self.add_history(
            target.id, target.hash, target.name, "manual_recheck",
            "Manual action: Force integrity recheck & resume initiated.",
            servarr_app=rec.servarr_app if rec else None,
            success=(recheck_ok and resume_ok)
        )
        return recheck_ok and resume_ok

    async def manual_failover(self, identifier: str) -> bool:
        """Manually trigger immediate blocklist, delete, and replacement search."""
        torrents = await self.transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            return False

        t_key = target.hash.lower() if target.hash else target.id
        rec = self.stalled_records.get(t_key)

        # Correlate with Servarr if not already correlated
        if not rec or not rec.servarr_queue_id:
            servarr_queues = await self.get_servarr_queues()
            servarr_match = servarr_queues.get(target.hash.lower()) if target.hash else None
            if servarr_match:
                if not rec:
                    rec = StalledRecord(target.id, target.hash)
                    self.stalled_records[t_key] = rec
                rec.servarr_app = servarr_match[0].app
                rec.servarr_queue_id = servarr_match[0].id
                rec.raw_servarr_record = servarr_match[1]

        if not rec:
            rec = StalledRecord(target.id, target.hash)
            self.stalled_records[t_key] = rec

        return await self._execute_stage_2_failover(target, rec)

    async def manual_reannounce(self, identifier: str) -> bool:
        """Manually trigger re-announce."""
        torrents = await self.transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            return False
        return await self.transmission.reannounce_torrents([target.id])
