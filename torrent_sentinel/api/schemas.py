from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class TorrentStatus(BaseModel):
    id: str
    name: str
    status: str
    rate_download: float
    rate_upload: float
    peers_connected: int
    peers_sending_to_us: int
    error: Optional[str] = None

class RotationEventSummary(BaseModel):
    timestamp: datetime
    from_location: str
    to_location: str
    reason: str
    peer_delta: int

class ScoreboardEntry(BaseModel):
    location_id: str
    avg_peers: float
    success_count: int

class SystemStatus(BaseModel):
    daemon_running: bool
    current_location: Optional[str]
    active_torrents: int
    last_rotation: Optional[datetime]
