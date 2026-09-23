import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional
import asyncio

from torrent_sentinel.config import settings
from torrent_sentinel.logging_config import setup_logging
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.vpn import get_vpn_adapter
from torrent_sentinel.api.schemas import (
    SystemStatus, 
    TorrentStatus, 
    RotationEventSummary, 
    ScoreboardEntry,
    BoostEventSummary,
    AutoFailoverToggleRequest,
    VpnRotationToggleRequest,
    RotateRequest,
    WebUILink
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
                auto_failover = await daemon_instance.booster.is_auto_failover_enabled()
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
    except Exception as e:
        logger.debug("API get_status: Error reading daemon state: %s", e)

    try:
        history = await storage.get_history()
        if history:
            last_rot_time = history[0].timestamp
    except Exception:
        pass

    return SystemStatus(
        daemon_running=(daemon_instance is not None and daemon_instance.running),
        current_location=current_loc,
        active_torrents=len(torrents),
        last_rotation=last_rot_time,
        boost_enabled=settings.BOOST_ENABLED,
        auto_failover_enabled=auto_failover,
        auto_vpn_rotation_enabled=auto_vpn_rotation,
        vpn_rotation_paused=not auto_vpn_rotation,
        cached_trackers_count=cached_trackers,
        healthy_trackers_count=healthy_trackers,
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
                    is_private=u.is_private
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

# Serve the frontend static files
import os
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(INDEX_FILE)
