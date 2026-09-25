import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional
import asyncio
import inspect

from torrent_sentinel.config import settings
from torrent_sentinel.logging_config import setup_logging
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.vpn import get_vpn_adapter
from torrent_sentinel.api.schemas import (
    SystemStatus, 
    TorrentStatus, 
    RotationEventSummary, 
    ScoreboardEntry,
    BoostEventSummary,
    AutoFailoverToggleRequest,
    LLMAssistedFailoverToggleRequest,
    VpnRotationToggleRequest,
    RotateRequest,
    JudgeTorrentRequest,
    LLMChatRequest,
    LLMChatResponse,
    WebUILink,
    AutopilotModeToggleRequest,
    AutopilotTriggerRequest,
    AutopilotStatusResponse,
    ModelSelectRequest,
    AvailableModelsResponse
)
from torrent_sentinel.models import (
    TorrentJudgement,
    SwarmAssessment,
    RecommendedAction,
    AutopilotMode
)


# Initialize logging for the web server
setup_logging()
logger = logging.getLogger("torrent_sentinel.api")

app = FastAPI(title="Torrent Sentinel API")

# Global state for the running daemon
daemon_instance: Optional[SentinelDaemon] = None
storage = Storage()

@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI Web Dashboard & API starting up...")
    await storage.initialize()
    global daemon_instance
    adapter = get_vpn_adapter()
    daemon_instance = SentinelDaemon(adapter)
    logger.info("Launching background SentinelDaemon task...")
    asyncio.create_task(daemon_instance.run())

def get_configured_web_uis() -> List[WebUILink]:
    """Return list of configured external Web UI links (Transmission, Radarr, Sonarr, etc.)."""
    links: List[WebUILink] = []

    # Transmission
    if settings.TRANSMISSION_WEB_ENABLED:
        if settings.TRANSMISSION_WEB_URL and settings.TRANSMISSION_WEB_URL.strip():
            links.append(WebUILink(
                name="Transmission",
                key="transmission",
                url=settings.TRANSMISSION_WEB_URL.strip(),
                icon="download",
                description="Torrent Client"
            ))
        elif settings.TRANSMISSION_HOST and settings.TRANSMISSION_PORT:
            links.append(WebUILink(
                name="Transmission",
                key="transmission",
                url=f"http://{settings.TRANSMISSION_HOST}:{settings.TRANSMISSION_PORT}/transmission/web/",
                icon="download",
                description="Torrent Client"
            ))

    # Radarr
    if settings.RADARR_URL and settings.RADARR_URL.strip():
        links.append(WebUILink(
            name="Radarr",
            key="radarr",
            url=settings.RADARR_URL.strip().rstrip("/"),
            icon="film",
            description="Movies"
        ))

    # Sonarr
    if settings.SONARR_URL and settings.SONARR_URL.strip():
        links.append(WebUILink(
            name="Sonarr",
            key="sonarr",
            url=settings.SONARR_URL.strip().rstrip("/"),
            icon="tv",
            description="TV Series"
        ))

    # Lidarr
    if settings.LIDARR_URL and settings.LIDARR_URL.strip():
        links.append(WebUILink(
            name="Lidarr",
            key="lidarr",
            url=settings.LIDARR_URL.strip().rstrip("/"),
            icon="music",
            description="Music"
        ))

    # Prowlarr
    if settings.PROWLARR_URL and settings.PROWLARR_URL.strip():
        links.append(WebUILink(
            name="Prowlarr",
            key="prowlarr",
            url=settings.PROWLARR_URL.strip().rstrip("/"),
            icon="search",
            description="Indexers"
        ))

    # Bazarr
    if settings.BAZARR_URL and settings.BAZARR_URL.strip():
        links.append(WebUILink(
            name="Bazarr",
            key="bazarr",
            url=settings.BAZARR_URL.strip().rstrip("/"),
            icon="captions",
            description="Subtitles"
        ))

    # Readarr
    if settings.READARR_URL and settings.READARR_URL.strip():
        links.append(WebUILink(
            name="Readarr",
            key="readarr",
            url=settings.READARR_URL.strip().rstrip("/"),
            icon="book-open",
            description="Books"
        ))

    return links

@app.get("/api/web-uis", response_model=List[WebUILink])
async def get_web_uis():
    return get_configured_web_uis()

@app.get("/api/status", response_model=SystemStatus)
async def get_status():
    torrents = []
    try:
        client = TransmissionClient()
        torrents = await client.get_torrents()
    except Exception as e:
        logger.warning("API get_status: Could not reach Transmission: %s", e)

    current_loc = "Unknown"
    last_rot_time = None
    auto_failover = settings.AUTO_FAILOVER_ENABLED
    llm_assisted = settings.LLM_ASSISTED_FAILOVER
    auto_vpn_rotation = settings.AUTO_VPN_ROTATION_ENABLED
    cached_trackers = 0
    healthy_trackers = 0

    try:
        if daemon_instance:
            if daemon_instance.vpn_adapter:
                profile = await daemon_instance.vpn_adapter.get_current_profile()
                if profile:
                    current_loc = profile.name
            if hasattr(daemon_instance, "booster") and daemon_instance.booster:
                if hasattr(daemon_instance.booster, "is_auto_failover_enabled"):
                    try:
                        res = daemon_instance.booster.is_auto_failover_enabled()
                        if inspect.isawaitable(res):
                            auto_failover = await res
                        elif isinstance(res, bool):
                            auto_failover = res
                    except Exception:
                        pass
                if hasattr(daemon_instance.booster, "is_llm_assisted_failover_enabled"):
                    try:
                        res = daemon_instance.booster.is_llm_assisted_failover_enabled()
                        if inspect.isawaitable(res):
                            llm_assisted = await res
                        elif isinstance(res, bool):
                            llm_assisted = res
                    except Exception:
                        pass
            if hasattr(daemon_instance, "is_vpn_rotation_enabled"):
                try:
                    res = daemon_instance.is_vpn_rotation_enabled()
                    if asyncio.iscoroutine(res):
                        auto_vpn_rotation = await res
                    elif isinstance(res, bool):
                        auto_vpn_rotation = res
                except Exception:
                    pass
            if daemon_instance.tracker_service:
                trackers = daemon_instance.tracker_service.get_trackers()
                cached_trackers = len(trackers)
                healthy_trackers = len(trackers)
                if hasattr(daemon_instance.tracker_service, "get_health_summary"):
                    try:
                        summary = daemon_instance.tracker_service.get_health_summary()
                        if isinstance(summary, dict) and "total_discovered" in summary:
                            cached_trackers = summary.get("total_discovered", len(trackers))
                            healthy_trackers = summary.get("healthy_count", len(trackers))
                    except Exception:
                        pass
        else:
            val = await storage.get_setting("auto_vpn_rotation_enabled")
            if val is not None:
                auto_vpn_rotation = (val.lower() == "true")
            val_llm = await storage.get_setting("llm_assisted_failover_enabled")
            if val_llm is not None:
                llm_assisted = (val_llm.lower() in ("true", "1", "yes"))
    except Exception as e:
        logger.debug("API get_status: Error reading daemon state: %s", e)

    try:
        history = await storage.get_history()
        if history:
            last_rot_time = history[0].timestamp
    except Exception:
        pass

    autopilot_mode = "off"
    try:
        if daemon_instance and hasattr(daemon_instance, "autopilot"):
            ap_mode_enum = await daemon_instance.autopilot.get_mode()
            autopilot_mode = ap_mode_enum.value
        else:
            autopilot_mode = await storage.get_autopilot_mode()
    except Exception:
        pass

    active_model = settings.OLLAMA_MODEL
    try:
        if daemon_instance and hasattr(daemon_instance, "ollama") and daemon_instance.ollama:
            active_model = await daemon_instance.ollama.get_active_model()
        else:
            active_model = await storage.get_ollama_model()
    except Exception:
        pass

    return SystemStatus(
        daemon_running=(daemon_instance is not None and daemon_instance.running),
        current_location=current_loc,
        active_torrents=len(torrents),
        last_rotation=last_rot_time,
        boost_enabled=settings.BOOST_ENABLED,
        auto_failover_enabled=auto_failover,
        llm_assisted_failover_enabled=llm_assisted,
        auto_vpn_rotation_enabled=auto_vpn_rotation,
        vpn_rotation_paused=not auto_vpn_rotation,
        cached_trackers_count=cached_trackers,
        healthy_trackers_count=healthy_trackers,
        ollama_enabled=settings.OLLAMA_ENABLED,
        ollama_model=active_model,
        autopilot_mode=autopilot_mode,
        web_uis=get_configured_web_uis()
    )

@app.get("/api/torrents", response_model=List[TorrentStatus])
async def get_torrents():
    try:
        if daemon_instance and daemon_instance.booster:
            unified = await daemon_instance.booster.get_unified_queue()
            return [
                TorrentStatus(
                    id=u.id,
                    hash=u.hash,
                    name=u.name,
                    status=u.status,
                    progress=u.progress,
                    rate_download=u.rate_download,
                    rate_upload=u.rate_upload,
                    peers_connected=u.peers_connected,
                    peers_sending_to_us=u.peers_sending_to_us,
                    num_seeds=u.num_seeds,
                    num_leechs=u.num_leechs,
                    error=u.error_string if u.is_errored else None,
                    boost_state=u.boost_state.value,
                    status_message=u.status_message,
                    servarr_app=u.servarr_app.value if u.servarr_app else None,
                    servarr_title=u.servarr_title,
                    servarr_queue_id=u.servarr_queue_id,
                    first_stalled_at=u.first_stalled_at,
                    boosted_at=u.boosted_at,
                    grace_period_expires_at=u.grace_period_expires_at,
                    is_private=u.is_private,
                    latest_judgement=u.latest_judgement.model_dump() if u.latest_judgement else None
                ) for u in unified
            ]


        client = TransmissionClient()
        torrents = await client.get_torrents()
        return [
            TorrentStatus(
                id=t.id,
                hash=t.hash,
                name=t.name,
                status=t.status,
                progress=t.progress,
                rate_download=t.rate_download,
                rate_upload=t.rate_upload,
                peers_connected=t.peers_connected,
                peers_sending_to_us=t.peers_sending_to_us,
                num_seeds=t.peers_sending_to_us,
                num_leechs=max(t.peers_connected - t.peers_sending_to_us, 0),
                error=(t.error_string or f"Error {t.error}") if (t.error is not None and t.error != 0 and str(t.error) != "0") else None,
                is_private=getattr(t, "is_private", False)
            ) for t in torrents
        ]
    except Exception as e:
        logger.error("API get_torrents failed: %s", e)
        return []

@app.post("/api/torrents/{identifier}/boost")
async def boost_torrent(identifier: str):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon or booster not initialized")
    success = await daemon_instance.booster.manual_boost(identifier)
    if not success:
        raise HTTPException(status_code=404, detail=f"Torrent '{identifier}' not found in client")
    return {"status": "success", "message": f"Trackers injected and re-announce sent to {identifier}"}

@app.post("/api/torrents/{identifier}/recheck")
async def recheck_torrent(identifier: str):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon or booster not initialized")
    success = await daemon_instance.booster.manual_recheck(identifier)
    if not success:
        raise HTTPException(status_code=404, detail=f"Torrent '{identifier}' not found or recheck failed")
    return {"status": "success", "message": f"Integrity recheck and resume dispatched for {identifier}"}

@app.post("/api/torrents/{identifier}/reannounce")
async def reannounce_torrent(identifier: str):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon or booster not initialized")
    success = await daemon_instance.booster.manual_reannounce(identifier)
    return {"status": "success" if success else "failed"}

@app.post("/api/torrents/{identifier}/failover")
async def failover_torrent(identifier: str):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon or booster not initialized")
    success = await daemon_instance.booster.manual_failover(identifier)
    if not success:
        raise HTTPException(status_code=404, detail=f"Torrent '{identifier}' not found or failover failed")
    return {"status": "success", "message": f"Failover executed for {identifier}"}

@app.post("/api/settings/auto-failover")
async def toggle_auto_failover(req: AutoFailoverToggleRequest):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon not initialized")
    await daemon_instance.booster.set_auto_failover_enabled(req.enabled)
    logger.info("Auto-failover setting updated to: %s", req.enabled)
    return {"status": "success", "auto_failover_enabled": req.enabled}

@app.post("/api/settings/llm-assisted-failover")
async def toggle_llm_assisted_failover(req: LLMAssistedFailoverToggleRequest):
    if not daemon_instance or not daemon_instance.booster:
        raise HTTPException(status_code=503, detail="Daemon not initialized")
    await daemon_instance.booster.set_llm_assisted_failover_enabled(req.enabled)
    logger.info("LLM-assisted failover setting updated to: %s", req.enabled)
    return {"status": "success", "llm_assisted_failover_enabled": req.enabled}

@app.post("/api/torrents/{identifier}/judge")
async def judge_torrent_endpoint(identifier: str, req: Optional[JudgeTorrentRequest] = None):
    if not daemon_instance or not daemon_instance.diagnostics:
        raise HTTPException(status_code=503, detail="Sentinel daemon diagnostics service not ready")

    # 1. Check if cached judgement exists within TTL if not forced
    if req and not req.force_fresh and not req.user_prompt:
        cached = await storage.get_latest_judgement(identifier)
        if cached:
            from datetime import timezone
            age_min = (datetime.now(timezone.utc) - cached.timestamp).total_seconds() / 60.0
            if age_min < settings.LLM_CACHE_TTL_MINUTES:
                return cached.model_dump()

    # 2. Fetch fresh torrent data from Transmission
    torrents = await daemon_instance.transmission.get_torrents()
    target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Torrent '{identifier}' not found in Transmission")

    # 3. Gather booster and servarr metadata
    stalled_rec = None
    servarr_match = None
    vpn_info = {}

    if daemon_instance.booster:
        t_key = target.hash.lower() if target.hash else target.id
        stalled_rec = daemon_instance.booster.stalled_records.get(t_key)
        servarr_queue = await daemon_instance.booster.get_servarr_queues()
        servarr_match = servarr_queue.get(target.hash.lower()) if target.hash else None

    if daemon_instance.vpn_adapter:
        prof = await daemon_instance.vpn_adapter.get_current_profile()
        if prof:
            vpn_info = {"current_location": prof.name, "endpoint": prof.endpoint}

    # 4. Invoke LLM Judge
    user_prompt = req.user_prompt if req else None
    judgement = await daemon_instance.diagnostics.judge_torrent(
        torrent=target,
        stalled_rec=stalled_rec,
        servarr_match=servarr_match,
        vpn_info=vpn_info,
        user_prompt=user_prompt
    )

    if not judgement:
        raise HTTPException(status_code=502, detail="Failed to obtain judgement from LLM (Ollama may be offline or unresponsive)")

    # 5. Persist to storage
    await storage.record_judgement(judgement)

    if stalled_rec:
        stalled_rec.status_message = f"AI [{judgement.verdict.value}]: {judgement.action_explanation}"

    return judgement.model_dump()

@app.get("/api/torrents/{identifier}/judgement")
async def get_torrent_judgement_endpoint(identifier: str):
    judgement = await storage.get_latest_judgement(identifier)
    if not judgement:
        raise HTTPException(status_code=404, detail=f"No AI judgement found for torrent '{identifier}'")
    return judgement.model_dump()

@app.get("/api/torrents/{identifier}/judgements")
async def get_torrent_judgements_history_endpoint(identifier: str, limit: int = 10):
    judgements = await storage.get_judgements_for_torrent(identifier, limit=limit)
    return [j.model_dump() for j in judgements]

@app.post("/api/llm/assess")
async def assess_swarm_endpoint():
    if not daemon_instance or not daemon_instance.diagnostics:
        raise HTTPException(status_code=503, detail="Sentinel daemon diagnostics service not ready")

    torrents = await daemon_instance.transmission.get_torrents()
    if not torrents:
        return {
            "overall_summary": "No active torrents in client.",
            "vpn_health_verdict": "healthy",
            "should_rotate_vpn": False,
            "vpn_reasoning": "Torrent queue is empty; no swarm traffic to evaluate.",
            "torrent_judgements": [],
            "recommended_actions": []
        }

    vpn_info = {}
    if daemon_instance.vpn_adapter:
        prof = await daemon_instance.vpn_adapter.get_current_profile()
        if prof:
            vpn_info = {"current_location": prof.name, "endpoint": prof.endpoint}

    stalled_records = daemon_instance.booster.stalled_records if daemon_instance.booster else {}
    assessment = await daemon_instance.diagnostics.assess_swarm(
        torrents=torrents,
        stalled_records=stalled_records,
        vpn_info=vpn_info
    )

    if not assessment:
        raise HTTPException(status_code=502, detail="Failed to obtain swarm assessment from LLM")

    for j in assessment.torrent_judgements:
        await storage.record_judgement(j)

    return assessment.model_dump()

@app.post("/api/llm/chat", response_model=LLMChatResponse)
async def chat_about_downloads_endpoint(req: LLMChatRequest):
    if not daemon_instance or not daemon_instance.ollama:
        raise HTTPException(status_code=503, detail="Ollama service not available")

    torrents = []
    try:
        torrents = await daemon_instance.transmission.get_torrents()
    except Exception:
        pass

    vpn_loc = "Unknown"
    if daemon_instance.vpn_adapter:
        prof = await daemon_instance.vpn_adapter.get_current_profile()
        if prof:
            vpn_loc = prof.name

    stalled_count = 0
    downloading_count = 0
    completed_seeding_count = 0
    problematic_torrents = []
    active_downloading = []

    min_rate_bytes = settings.MIN_DOWNLOAD_RATE_KBPS * 1024.0

    for t in torrents:
        is_complete = t.progress >= 1.0 or t.status.lower() in ("seed", "seeding", "stopped", "paused")
        has_error = bool(t.error or t.error_string)
        is_stalled = (t.progress < 1.0 and (t.peers_connected < settings.MIN_SEEDS or t.rate_download < min_rate_bytes))

        if is_complete and not has_error:
            completed_seeding_count += 1
            continue

        item_summary = {
            "id": t.id,
            "name": t.name[:50],
            "progress_percent": round(t.progress * 100, 1),
            "download_kbps": round(t.rate_download / 1024, 1),
            "peers": t.peers_connected,
            "seeds": t.peers_sending_to_us,
            "error": t.error_string or t.error,
            "is_stalled": is_stalled,
            "is_private": getattr(t, "is_private", False)
        }

        if is_stalled or has_error:
            stalled_count += 1
            problematic_torrents.append(item_summary)
        else:
            downloading_count += 1
            active_downloading.append(item_summary)

    # Keep top 15 stalled/problematic and top 10 downloading to strictly bound prompt size
    active_torrents_summary = problematic_torrents[:15] + active_downloading[:10]

    context = {
        "current_vpn_location": vpn_loc,
        "queue_overview": {
            "total_torrents": len(torrents),
            "stalled_or_errored_count": stalled_count,
            "actively_downloading_count": downloading_count,
            "healthy_completed_seeding_count": completed_seeding_count
        },
        "total_torrents": len(torrents),
        "stalled_torrents_count": stalled_count,
        "torrents": active_torrents_summary,
        "auto_vpn_rotation_enabled": await daemon_instance.is_vpn_rotation_enabled(),
        "auto_failover_enabled": await daemon_instance.booster.is_auto_failover_enabled() if daemon_instance.booster else False
    }

    reply = await daemon_instance.ollama.chat_about_downloads(
        message=req.message,
        context=context,
        history=req.history
    )
    return LLMChatResponse(response=reply)

@app.get("/api/llm/models", response_model=AvailableModelsResponse)
async def get_available_models_endpoint():
    current = settings.OLLAMA_MODEL
    ollama_client = daemon_instance.ollama if (daemon_instance and daemon_instance.ollama) else OllamaClient(storage=storage)
    try:
        current = await ollama_client.get_active_model()
    except Exception:
        current = await storage.get_ollama_model()

    available = []
    try:
        available = await ollama_client.get_available_models()
    except Exception as e:
        logger.warning("Could not fetch available Ollama models: %s", e)

    if current and current not in available:
        available.insert(0, current)

    return AvailableModelsResponse(current_model=current, available_models=available)

@app.post("/api/llm/model", response_model=AvailableModelsResponse)
async def set_active_model_endpoint(req: ModelSelectRequest):
    new_model = req.model.strip()
    if not new_model:
        raise HTTPException(status_code=400, detail="Model name cannot be empty")

    await storage.set_ollama_model(new_model)
    if daemon_instance and daemon_instance.ollama:
        await daemon_instance.ollama.set_active_model(new_model)

    ollama_client = daemon_instance.ollama if (daemon_instance and daemon_instance.ollama) else OllamaClient(storage=storage)
    available = []
    try:
        available = await ollama_client.get_available_models()
    except Exception:
        pass

    if new_model not in available:
        available.insert(0, new_model)

    logger.info("Runtime Ollama model updated to: %s", new_model)
    return AvailableModelsResponse(current_model=new_model, available_models=available)


@app.post("/api/settings/vpn-rotation")
async def toggle_vpn_rotation(req: VpnRotationToggleRequest):
    enabled = req.enabled if req.enabled is not None else (not req.paused if req.paused is not None else True)
    if daemon_instance:
        await daemon_instance.set_vpn_rotation_enabled(enabled)
    else:
        await storage.set_setting("auto_vpn_rotation_enabled", str(enabled).lower())
    logger.info("VPN rotation setting updated: enabled=%s (paused=%s)", enabled, not enabled)
    return {
        "status": "success",
        "auto_vpn_rotation_enabled": enabled,
        "vpn_rotation_paused": not enabled
    }

@app.post("/api/settings/auto-vpn-rotation")
async def toggle_auto_vpn_rotation(req: VpnRotationToggleRequest):
    return await toggle_vpn_rotation(req)

@app.post("/api/vpn/pause")
async def pause_vpn_rotation():
    return await toggle_vpn_rotation(VpnRotationToggleRequest(paused=True))

@app.post("/api/vpn/resume")
async def resume_vpn_rotation():
    return await toggle_vpn_rotation(VpnRotationToggleRequest(paused=False))


@app.get("/api/boost-history", response_model=List[BoostEventSummary])
async def get_boost_history():
    try:
        events = await storage.get_boost_events()
        return [
            BoostEventSummary(
                id=e.id,
                timestamp=e.timestamp,
                torrent_id=e.torrent_id,
                torrent_hash=e.torrent_hash,
                torrent_name=e.torrent_name,
                action=e.action,
                details=e.details,
                servarr_app=e.servarr_app.value if e.servarr_app else None,
                success=e.success
            ) for e in events
        ]
    except Exception as e:
        logger.error("API get_boost_history failed: %s", e)
        return []

@app.get("/api/trackers")
async def get_trackers():
    trackers = []
    summary = {}
    if daemon_instance and daemon_instance.tracker_service:
        trackers = daemon_instance.tracker_service.get_trackers()
        if hasattr(daemon_instance.tracker_service, "get_health_summary"):
            try:
                s = daemon_instance.tracker_service.get_health_summary()
                if isinstance(s, dict):
                    summary = s
            except Exception:
                pass
    return {
        "status": "success",
        "total": summary.get("total_discovered", len(trackers)),
        "healthy_count": summary.get("healthy_count", len(trackers)),
        "last_refreshed": summary.get("last_refreshed"),
        "last_probed": summary.get("last_probed"),
        "trackers": trackers,
        "health_details": summary.get("trackers", []),
        "sources": settings.TRACKER_LIST_URLS
    }

@app.post("/api/trackers/refresh")
async def refresh_trackers(background_tasks: BackgroundTasks):
    if not daemon_instance or not daemon_instance.tracker_service:
        raise HTTPException(status_code=503, detail="Sentinel daemon tracker service is not active.")
    background_tasks.add_task(daemon_instance.tracker_service.refresh_trackers, probe=True)
    return {
        "status": "success",
        "message": "Tracker refresh and probe initiated in background."
    }

@app.get("/api/history", response_model=List[RotationEventSummary])
async def get_history():
    try:
        events = await storage.get_history()
        return [
            RotationEventSummary(
                timestamp=e.timestamp,
                from_location=e.from_location,
                to_location=e.to_location,
                reason=e.reason,
                peer_delta=e.peers_after - e.peers_before
            ) for e in events
        ]
    except Exception as e:
        logger.error("API get_history failed: %s", e)
        return []

@app.get("/api/scoreboard", response_model=List[ScoreboardEntry])
async def get_scoreboard():
    try:
        rows = await storage.get_scoreboard()
        return [ScoreboardEntry(
            location_id=r["location_id"],
            avg_peers=float(r["avg_peers"]),
            success_count=int(r["success_count"])
        ) for r in rows]
    except Exception as e:
        logger.debug("API get_scoreboard returned empty: %s", e)
        return []

@app.get("/api/locations")
async def get_locations():
    locations = []
    current_id = None
    if daemon_instance and daemon_instance.vpn_adapter:
        try:
            available = await daemon_instance.vpn_adapter.get_available_locations()
            current = await daemon_instance.vpn_adapter.get_current_profile()
            current_id = current.id if current else None
            scoreboard_rows = await storage.get_scoreboard()
            scores_map = {r["location_id"]: r for r in scoreboard_rows}
            locations = [
                {
                    "id": p.id,
                    "name": p.name,
                    "is_current": (p.id == current_id),
                    "avg_peers": scores_map[p.id]["avg_peers"] if p.id in scores_map else None,
                    "success_count": scores_map[p.id]["success_count"] if p.id in scores_map else 0
                }
                for p in available
            ]
        except Exception as e:
            logger.error("API get_locations error: %s", e)
    return {
        "status": "success",
        "total": len(locations),
        "current": current_id,
        "locations": locations
    }

@app.post("/api/rotate")
async def trigger_rotation(background_tasks: BackgroundTasks, request: Optional[RotateRequest] = None):
    global daemon_instance
    if not daemon_instance:
        logger.warning("API trigger_rotation rejected: Daemon instance not ready.")
        raise HTTPException(status_code=503, detail="Daemon not running")
    
    preferred = request.location if request else None
    logger.info("Manual VPN rotation requested via Web Dashboard / API (preferred: %s).", preferred)

    async def run_rotation(pref_loc: Optional[str] = None):
        adapter = daemon_instance.vpn_adapter
        current_profile = await adapter.get_current_profile()
        available = await adapter.get_available_locations()
        target = await daemon_instance.decision_engine.select_next_profile(
            current_profile=current_profile,
            available=available,
            preferred_location=pref_loc
        )
        if target:
            logger.info("API Trigger: executing rotation to '%s' (%s)...", target.name, target.id)
            await daemon_instance.decision_engine.execute_rotation(current_profile, target, "Manual Web Dashboard Trigger")
        else:
            logger.warning("API Trigger: No alternate location available to rotate to.")

    background_tasks.add_task(run_rotation, preferred)
    return {"message": "Rotation triggered"}

@app.get("/api/autopilot/status")
async def get_autopilot_status():
    mode = "off"
    last_run = None
    last_plan = None
    if daemon_instance and hasattr(daemon_instance, "autopilot"):
        ap = daemon_instance.autopilot
        mode_enum = await ap.get_mode()
        mode = mode_enum.value
        last_run = ap.last_run_time
        if ap.last_plan:
            last_plan = ap.last_plan.model_dump()
    else:
        mode = await storage.get_autopilot_mode()

    events = await storage.get_autopilot_events(limit=25)
    return {
        "status": "success",
        "mode": mode,
        "last_run": last_run,
        "last_plan": last_plan,
        "recent_events": [e.model_dump() for e in events]
    }

@app.post("/api/autopilot/mode")
async def set_autopilot_mode_endpoint(req: AutopilotModeToggleRequest):
    try:
        mode_enum = AutopilotMode(req.mode.lower().strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid autopilot mode: {req.mode}. Allowed: off, advisory, full")

    if daemon_instance and hasattr(daemon_instance, "autopilot"):
        await daemon_instance.autopilot.set_mode(mode_enum)
    else:
        await storage.set_autopilot_mode(mode_enum.value)

    logger.info("API: Autopilot mode changed to '%s'", mode_enum.value)
    return {"status": "success", "mode": mode_enum.value}

@app.post("/api/autopilot/trigger")
async def trigger_autopilot_cycle(req: Optional[AutopilotTriggerRequest] = None):
    if not daemon_instance or not hasattr(daemon_instance, "autopilot"):
        raise HTTPException(status_code=503, detail="Daemon or Autopilot engine not ready")

    mode_override = None
    if req and req.mode:
        try:
            mode_override = AutopilotMode(req.mode.lower().strip())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid mode override: {req.mode}")

    force = req.force if req else True
    torrents = await daemon_instance.transmission.get_torrents()
    plan = await daemon_instance.autopilot.run_autopilot_cycle(
        torrents=torrents,
        force=force,
        mode_override=mode_override
    )

    if not plan:
        return {"status": "skipped_or_failed", "message": "Autopilot cycle skipped or produced no plan"}

    return {
        "status": "success",
        "plan": plan.model_dump()
    }

@app.get("/api/autopilot/events")
async def get_autopilot_events_endpoint(limit: int = 50):
    events = await storage.get_autopilot_events(limit=limit)
    return {
        "status": "success",
        "total": len(events),
        "events": [e.model_dump() for e in events]
    }

# Serve the frontend static files
import os
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(INDEX_FILE)
