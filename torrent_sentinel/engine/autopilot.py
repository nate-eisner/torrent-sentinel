import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from torrent_sentinel.config import settings
from torrent_sentinel.models import (
    TorrentInfo,
    AutopilotMode,
    AutopilotAction,
    AutopilotPlan,
    AutopilotEvent,
    RecommendedAction,
    LocationProfile
)
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.engine.booster import TorrentBooster
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.vpn.base import BaseVPNAdapter
from torrent_sentinel.notifications.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)

class AutopilotEngine:
    def __init__(
        self,
        transmission: TransmissionClient,
        booster: TorrentBooster,
        decision_engine: DecisionEngine,
        vpn_adapter: BaseVPNAdapter,
        storage: Storage,
        ollama: OllamaClient,
        notifications: Optional[NotificationDispatcher] = None
    ):
        self.transmission = transmission
        self.booster = booster
        self.decision_engine = decision_engine
        self.vpn_adapter = vpn_adapter
        self.storage = storage
        self.ollama = ollama
        self.notifications = notifications or NotificationDispatcher()

        self.last_run_time: Optional[datetime] = None
        self.last_plan: Optional[AutopilotPlan] = None
        self.last_vpn_rotation_time: Optional[datetime] = None
        self._lock = asyncio.Lock()

    async def get_mode(self) -> AutopilotMode:
        mode_str = await self.storage.get_autopilot_mode()
        try:
            return AutopilotMode(mode_str.lower())
        except ValueError:
            return AutopilotMode.OFF

    async def set_mode(self, mode: AutopilotMode):
        logger.info("Setting Autopilot mode to: %s", mode.value)
        await self.storage.set_autopilot_mode(mode.value)

    def should_run(self, now: Optional[datetime] = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        if self.last_run_time is None:
            return True
        interval_seconds = settings.AUTOPILOT_INTERVAL_MINUTES * 60
        return (now - self.last_run_time).total_seconds() >= interval_seconds

    async def build_fleet_telemetry(self, torrents: List[TorrentInfo]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Constructs queue telemetry and VPN context payloads for the LLM."""
        now = datetime.now(timezone.utc)
        
        # 1. VPN Context
        current_loc = await self.vpn_adapter.get_current_profile()
        available_locs = await self.vpn_adapter.get_available_locations()
        
        # Recent rotations
        history = await self.storage.get_history(limit=10)
        recent_1h_count = 0
        for e in history:
            e_ts = e.timestamp if e.timestamp.tzinfo is not None else e.timestamp.replace(tzinfo=timezone.utc)
            if (now - e_ts).total_seconds() < 3600:
                recent_1h_count += 1
        
        last_rot_ago_min = None
        if history:
            last_ts = history[0].timestamp
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            last_rot_ago_min = round((now - last_ts).total_seconds() / 60.0, 1)

        vpn_context = {
            "current_location": {
                "id": current_loc.id if current_loc else "unknown",
                "name": current_loc.name if current_loc else "unknown"
            },
            "available_locations": [{"id": l.id, "name": l.name} for l in available_locs],
            "rotations_past_hour": recent_1h_count,
            "minutes_since_last_rotation": last_rot_ago_min
        }

        # 2. Queue Telemetry
        total_active = len(torrents)
        downloading_count = sum(1 for t in torrents if t.status in ("downloading", "download"))
        seeding_count = sum(1 for t in torrents if t.status in ("seeding", "seed"))
        total_dl_rate = sum(getattr(t, "rate_download", 0.0) / 1024.0 for t in torrents)

        # Identify stalled/problematic torrents
        stalled_candidates = []
        for t in torrents:
            t_key = t.hash.lower() if t.hash else t.id
            rec = self.booster.stalled_records.get(t_key)
            is_stalled = False
            stalled_min = 0.0
            dl_rate_kbps = getattr(t, "rate_download", 0.0) / 1024.0

            if rec:
                is_stalled = True
                stalled_min = round((now - rec.first_stalled_at).total_seconds() / 60.0, 1)
            elif t.status in ("downloading", "download") and dl_rate_kbps < settings.MIN_DOWNLOAD_RATE_KBPS:
                is_stalled = True

            # Compact tracker summary
            tracker_summary = {"total": 0, "active": 0, "errors": 0}
            t_trackers = getattr(t, "trackers", None) or getattr(t, "tracker_stats", [])
            if t_trackers:
                tracker_summary["total"] = len(t_trackers)
                for tr in t_trackers:
                    if isinstance(tr, dict):
                        if tr.get("is_active") or tr.get("seeds", 0) > 0 or tr.get("peers", 0) > 0:
                            tracker_summary["active"] += 1
                        if tr.get("error_count", 0) > 0 or tr.get("last_announce_succeeded") is False:
                            tracker_summary["errors"] += 1
                    else:
                        if getattr(tr, "is_active", False) or getattr(tr, "seeds", 0) > 0 or getattr(tr, "peers", 0) > 0:
                            tracker_summary["active"] += 1
                        if getattr(tr, "error_count", 0) > 0 or getattr(tr, "last_announce_succeeded", True) is False:
                            tracker_summary["errors"] += 1

            eta_min = round(t.eta / 60.0, 1) if getattr(t, "eta", None) else None

            stalled_candidates.append({
                "id": t.id,
                "hash": t.hash,
                "name": t.name,
                "status": t.status,
                "progress_pct": round(t.progress * 100, 1),
                "download_rate_kbps": round(dl_rate_kbps, 1),
                "peers_connected": getattr(t, "peers_connected", 0),
                "peers_sending_to_us": getattr(t, "peers_sending_to_us", 0),
                "eta_minutes": eta_min,
                "is_stalled": is_stalled,
                "stalled_minutes": stalled_min,
                "is_private": getattr(t, "is_private", False),
                "trackers_summary": tracker_summary,
                "servarr_linked": bool(rec and rec.servarr_queue_id),
                "servarr_app": rec.servarr_app.value if rec and rec.servarr_app else None,
                "rescue_state": rec.state.value if rec else None,
                "boost_count": rec.boost_count if rec else 0
            })

        queue_telemetry = {
            "total_active": total_active,
            "downloading_count": downloading_count,
            "seeding_count": seeding_count,
            "stalled_count": sum(1 for c in stalled_candidates if c["is_stalled"]),
            "total_download_rate_kbps": round(total_dl_rate, 1),
            "torrents": stalled_candidates
        }

        return queue_telemetry, vpn_context

    async def run_autopilot_cycle(
        self,
        torrents: List[TorrentInfo],
        force: bool = False,
        mode_override: Optional[AutopilotMode] = None
    ) -> Optional[AutopilotPlan]:
        """Runs a complete Autopilot planning and execution cycle."""
        if self._lock.locked():
            logger.warning("Autopilot cycle already running; skipping concurrent invocation.")
            return self.last_plan

        async with self._lock:
            mode = mode_override or await self.get_mode()
            now = datetime.now(timezone.utc)

            if mode == AutopilotMode.OFF and not force:
                logger.debug("Autopilot is OFF; skipping cycle.")
                return None

            if not force and not self.should_run(now):
                logger.debug("Autopilot interval not elapsed; skipping cycle.")
                return self.last_plan

            logger.info("================== INITIATING AI AUTOPILOT CYCLE (Mode: %s) ==================", mode.value)
            self.last_run_time = now

            queue_telemetry, vpn_context = await self.build_fleet_telemetry(torrents)
            
            # Request flight plan from LLM
            plan = await self.ollama.generate_autopilot_plan(
                queue_telemetry=queue_telemetry,
                vpn_context=vpn_context,
                mode=mode
            )

            if not plan:
                logger.warning("Autopilot: Ollama returned no plan or encountered an error.")
                return None

            # Apply Deterministic Safety Guardrails
            await self._apply_guardrails(plan, torrents)

            # Execution Dispatch (Full vs Advisory)
            if mode == AutopilotMode.FULL:
                await self._execute_plan(plan, torrents)
            else:
                await self._simulate_plan(plan)

            self.last_plan = plan
            return plan

    async def _apply_guardrails(self, plan: AutopilotPlan, torrents: List[TorrentInfo]):
        """Evaluates hard guardrails and overrides plan decisions if safety boundaries are violated."""
        now = datetime.now(timezone.utc)
        torrent_map = {t.id: t for t in torrents}
        torrent_map.update({t.hash.lower(): t for t in torrents if t.hash})

        # 1. VPN Rotation Cooldown Guardrail
        if plan.should_rotate_vpn:
            cooldown_seconds = settings.AUTOPILOT_ROTATION_COOLDOWN_MINUTES * 60
            history = await self.storage.get_history(limit=1)
            if history:
                last_event_time = history[0].timestamp
                if last_event_time.tzinfo is None:
                    last_event_time = last_event_time.replace(tzinfo=timezone.utc)
                elapsed = (now - last_event_time).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining_min = round((cooldown_seconds - elapsed) / 60.0, 1)
                    logger.warning(
                        "Autopilot Guardrail Triggered: VPN rotation suppressed due to cooldown (%s min remaining).",
                        remaining_min
                    )
                    plan.should_rotate_vpn = False
                    plan.guardrails_applied.append(f"VPN rotation suppressed: cooldown active ({remaining_min}m remaining)")

        # 2. Action Guardrails (Confidence floor, max failovers, private immunity)
        failovers_scheduled = 0
        filtered_actions = []

        for action in plan.actions:
            t = torrent_map.get(action.target_id)
            if not t and action.target_id:
                t = torrent_map.get(action.target_id.lower())

            # Private Torrent Immunity
            if t and getattr(t, "is_private", False):
                if action.action_type in (RecommendedAction.FAILOVER, RecommendedAction.BOOST_TRACKERS):
                    guard_msg = f"Action '{action.action_type.value}' suppressed for private torrent '{t.name}'"
                    logger.warning("Autopilot Guardrail Triggered: %s", guard_msg)
                    plan.guardrails_applied.append(guard_msg)
                    action.action_type = RecommendedAction.WAIT
                    action.reasoning = f"[GUARDRAIL APPLIED] {guard_msg}. {action.reasoning}"

            # Failover Confidence Floor & Max Failovers per cycle
            if action.action_type == RecommendedAction.FAILOVER:
                if action.confidence < settings.AUTOPILOT_MIN_CONFIDENCE_FAILOVER:
                    guard_msg = (
                        f"Failover suppressed for '{action.target_name or action.target_id}': "
                        f"confidence {action.confidence:.2f} < floor {settings.AUTOPILOT_MIN_CONFIDENCE_FAILOVER:.2f}"
                    )
                    logger.warning("Autopilot Guardrail Triggered: %s", guard_msg)
                    plan.guardrails_applied.append(guard_msg)
                    action.action_type = RecommendedAction.WAIT
                    action.reasoning = f"[GUARDRAIL APPLIED] {guard_msg}. {action.reasoning}"
                elif failovers_scheduled >= settings.AUTOPILOT_MAX_FAILOVERS_PER_CYCLE:
                    guard_msg = (
                        f"Failover suppressed for '{action.target_name or action.target_id}': "
                        f"max {settings.AUTOPILOT_MAX_FAILOVERS_PER_CYCLE} failover(s) per cycle reached"
                    )
                    logger.warning("Autopilot Guardrail Triggered: %s", guard_msg)
                    plan.guardrails_applied.append(guard_msg)
                    action.action_type = RecommendedAction.WAIT
                    action.reasoning = f"[GUARDRAIL APPLIED] {guard_msg}. {action.reasoning}"
                else:
                    failovers_scheduled += 1

            filtered_actions.append(action)

        plan.actions = filtered_actions

    async def _simulate_plan(self, plan: AutopilotPlan):
        """Simulates plan execution in Advisory or Off mode, logging events without mutating state."""
        logger.info("[ADVISORY MODE] Logging simulated flight actions (no changes executed)...")
        for action in plan.actions:
            action.executed = False
            action.execution_result = f"Simulated in {plan.mode.value} mode"

            event = AutopilotEvent(
                plan_id=plan.id,
                mode=plan.mode.value,
                action_type=action.action_type.value,
                target_id=action.target_id,
                target_name=action.target_name,
                confidence=action.confidence,
                viability_score=action.viability_score,
                reasoning=action.reasoning,
                executed=False,
                execution_result=action.execution_result
            )
            await self.storage.record_autopilot_event(event)

        if plan.should_rotate_vpn:
            event = AutopilotEvent(
                plan_id=plan.id,
                mode=plan.mode.value,
                action_type="rotate_vpn",
                target_id=plan.preferred_vpn_location,
                target_name=f"VPN Gateway -> {plan.preferred_vpn_location or 'Best Location'}",
                confidence=1.0,
                viability_score=0.5,
                reasoning=plan.vpn_reasoning,
                executed=False,
                execution_result=f"Simulated VPN rotation in {plan.mode.value} mode"
            )
            await self.storage.record_autopilot_event(event)

    async def _execute_plan(self, plan: AutopilotPlan, torrents: List[TorrentInfo]):
        """Executes plan actions autonomously in Full Autopilot mode."""
        logger.info("[FULL AUTOPILOT] Executing autonomous fleet flight plan...")

        # 1. VPN Rotation Execution
        if plan.should_rotate_vpn:
            current_loc = await self.vpn_adapter.get_current_profile()
            available = await self.vpn_adapter.get_available_locations()
            
            target_profile: Optional[LocationProfile] = None
            if plan.preferred_vpn_location:
                pref = plan.preferred_vpn_location.lower().strip()
                target_profile = next((p for p in available if p.id.lower() == pref or p.name.lower() == pref), None)

            if not target_profile:
                target_profile = await self.decision_engine.find_best_location(current_loc, available)

            if target_profile and (not current_loc or target_profile.id != current_loc.id):
                logger.info(
                    "Autopilot executing VPN rotation: %s -> %s (Reason: %s)",
                    current_loc.name if current_loc else "None", target_profile.name, plan.vpn_reasoning
                )
                success = await self.decision_engine.execute_rotation(
                    current_loc, target_profile, reason=f"AI Autopilot: {plan.vpn_reasoning}"
                )
                self.last_vpn_rotation_time = datetime.now(timezone.utc)

                event = AutopilotEvent(
                    plan_id=plan.id,
                    mode=plan.mode.value,
                    action_type="rotate_vpn",
                    target_id=target_profile.id,
                    target_name=target_profile.name,
                    confidence=1.0,
                    viability_score=0.5,
                    reasoning=plan.vpn_reasoning,
                    executed=success,
                    execution_result="VPN rotated successfully" if success else "VPN rotation failed"
                )
                await self.storage.record_autopilot_event(event)
                if success:
                    await self.notifications.send_notification(
                        title="AI Autopilot: VPN Rotated",
                        message=f"Autopilot switched gateway to '{target_profile.name}'. Reason: {plan.vpn_reasoning}"
                    )

        # 2. Torrent Actions Execution
        for action in plan.actions:
            result_str = ""
            executed_ok = False
            
            try:
                if action.action_type == RecommendedAction.BOOST_TRACKERS:
                    executed_ok = await self.booster.manual_boost(action.target_id)
                    result_str = "Public trackers injected & reannounced" if executed_ok else "Boost failed (not found or error)"

                elif action.action_type == RecommendedAction.RECHECK:
                    executed_ok = await self.booster.manual_recheck(action.target_id)
                    result_str = "Integrity recheck & resume triggered" if executed_ok else "Recheck failed (not found or error)"

                elif action.action_type == RecommendedAction.REANNOUNCE:
                    executed_ok = await self.booster.manual_reannounce(action.target_id)
                    result_str = "Re-announce signal sent" if executed_ok else "Re-announce failed"

                elif action.action_type == RecommendedAction.FAILOVER:
                    executed_ok = await self.booster.manual_failover(action.target_id)
                    result_str = "Blocklisted & replacement search triggered in Servarr" if executed_ok else "Failover failed"
                    if executed_ok:
                        await self.notifications.send_notification(
                            title="AI Autopilot: Torrent Failover",
                            message=f"Dead torrent '{action.target_name}' failed over. Release blocklisted and replacement requested."
                        )

                elif action.action_type == RecommendedAction.WAIT:
                    # Check if probation extension was suggested
                    ext_min = action.parameters.get("extend_probation_minutes")
                    if ext_min and action.target_id:
                        t_match = next((t for t in torrents if t.id == action.target_id or t.hash.lower() == action.target_id.lower()), None)
                        if t_match:
                            t_key = t_match.hash.lower() if t_match.hash else t_match.id
                            rec = self.booster.stalled_records.get(t_key)
                            if rec and rec.grace_period_expires_at:
                                rec.grace_period_expires_at += timedelta(minutes=int(ext_min))
                                result_str = f"Probation extended by {ext_min} minutes"
                                executed_ok = True
                    if not result_str:
                        result_str = "Monitoring / wait observed"
                        executed_ok = True

            except Exception as e:
                logger.error("Error executing autopilot action %s: %s", action.action_type, e, exc_info=True)
                executed_ok = False
                result_str = f"Execution error: {e}"

            action.executed = executed_ok
            action.execution_result = result_str

            event = AutopilotEvent(
                plan_id=plan.id,
                mode=plan.mode.value,
                action_type=action.action_type.value,
                target_id=action.target_id,
                target_name=action.target_name,
                confidence=action.confidence,
                viability_score=action.viability_score,
                reasoning=action.reasoning,
                executed=executed_ok,
                execution_result=result_str
            )
            await self.storage.record_autopilot_event(event)
