from fastapi import FastAPI, HTTPException, BackgroundTasks
from typing import List, Optional
import asyncio

from torrent_sentinel.config import settings
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.vpn.mock import MockVPNAdapter
from torrent_sentinel.api.schemas import (
    SystemStatus, 
    TorrentStatus, 
    RotationEventSummary, 
    ScoreboardEntry
)

app = FastAPI(title="Torrent Sentinel API")

# Global state for the running daemon
daemon_instance: Optional[SentinelDaemon] = None
storage = Storage()

@app.on_event("startup")
async def startup_event():
    await storage.initialize()
    global daemon_instance
    # Using Mock for API stability in this environment
    daemon_instance = SentinelDaemon(MockVPNAdapter())
    asyncio.create_task(daemon_instance.run())

@app.get("/api/status", response_model=SystemStatus)
async def get_status():
    try:
        client = TransmissionClient()
        torrents = await client.get_torrents()
    except Exception:
        torrents = []

    # Get current location from mock adapter if possible
    current_loc = "Unknown"
    try:
        from torrent_sentinel.vpn.mock import MockVPNAdapter
        adapter = MockVPNAdapter()
        profile = await adapter.get_current_profile()
        if profile:
            current_loc = profile.name
    except Exception:
        pass

    return SystemStatus(
        daemon_running=True,
        current_location=current_loc,
        active_torrents=len(torrents),
        last_rotation=None
    )

@app.get("/api/torrents", response_model=List[TorrentStatus])
async def get_torrents():
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
            error=t.error
        ) for t in torrents
    ]

@app.get("/api/history", response_model=List[RotationEventSummary])
async def get_history():
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

@app.get("/api/scoreboard", response_model=List[ScoreboardEntry])
async def get_scoreboard():
    # Placeholder for scoreboard data
    return []

@app.post("/api/rotate")
async def trigger_rotation(background_tasks: BackgroundTasks):
    global daemon_instance
    if not daemon_instance:
        raise HTTPException(status_code=503, detail="Daemon not running")
    
    # Trigger rotation via background task to avoid blocking API response
    async def run_rotation():
        # In a real scenario, we'd fetch the current profile properly
        from torrent_sentinel.vpn.mock import MockVPNAdapter
        adapter = MockVPNAdapter()
        current_profile = await adapter.get_current_profile()
        
        # Find a target profile (just pick the first available that isn't current)
        available = await adapter.get_available_locations()
        target = next((p for p in available if p.id != (current_profile.id if current_profile else "")), None)
        
        if target:
            await daemon_instance.decision_engine.execute_rotation(current_profile, target, "Manual API Trigger")

    background_tasks.add_task(run_rotation)
    return {"message": "Rotation triggered"}

