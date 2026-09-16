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
    ScoreboardEntry
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
    try:
        if daemon_instance and daemon_instance.vpn_adapter:
            profile = await daemon_instance.vpn_adapter.get_current_profile()
            if profile:
                current_loc = profile.name
    except Exception as e:
        logger.debug("API get_status: Could not fetch active profile: %s", e)

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
        last_rotation=last_rot_time
    )

@app.get("/api/torrents", response_model=List[TorrentStatus])
async def get_torrents():
    try:
        client = TransmissionClient()
        torrents = await client.get_torrents()
        return [
            TorrentStatus(
                id=t.id,
                name=t.name,
                status=t.status,
                rate_download=t.rate_download,
                rate_upload=t.rate_upload,
                peers_connected=t.peers_connected,
                peers_sending_to_us=t.peers_sending_to_us,
                error=(t.error_string or f"Error {t.error}") if (t.error is not None and t.error != 0 and str(t.error) != "0") else None
            ) for t in torrents
        ]
    except Exception as e:
        logger.error("API get_torrents failed: %s", e)
        return []

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
        import aiosqlite
        async with aiosqlite.connect(storage.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT location_id, avg_peers, success_count FROM location_scores ORDER BY avg_peers DESC") as cursor:
                rows = await cursor.fetchall()
                return [ScoreboardEntry(
                    location_id=r["location_id"],
                    avg_peers=float(r["avg_peers"]),
                    success_count=int(r["success_count"])
                ) for r in rows]
    except Exception as e:
        logger.debug("API get_scoreboard returned empty: %s", e)
        return []

@app.post("/api/rotate")
async def trigger_rotation(background_tasks: BackgroundTasks):
    global daemon_instance
    if not daemon_instance:
        logger.warning("API trigger_rotation rejected: Daemon instance not ready.")
        raise HTTPException(status_code=503, detail="Daemon not running")
    
    logger.info("Manual VPN rotation requested via Web Dashboard / API.")

    async def run_rotation():
        adapter = daemon_instance.vpn_adapter
        current_profile = await adapter.get_current_profile()
        available = await adapter.get_available_locations()
        target = next((p for p in available if p.id != (current_profile.id if current_profile else "")), None)
        if target:
            logger.info("API Trigger: executing rotation to '%s'...", target.name)
            await daemon_instance.decision_engine.execute_rotation(current_profile, target, "Manual Web Dashboard Trigger")
        else:
            logger.warning("API Trigger: No alternate location available to rotate to.")

    background_tasks.add_task(run_rotation)
    return {"message": "Rotation triggered"}

# Serve the frontend static files
app.mount("/static", StaticFiles(directory="torrent_sentinel/api/static"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("torrent_sentinel/api/static/index.html")
