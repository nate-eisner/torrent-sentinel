from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class TorrentStatus(BaseModel):
    id: str
    hash: str = ""
    name: str
    status: str
    progress: float = 0.0
    rate_download: float = 0.0
    rate_upload: float = 0.0
    peers_connected: int = 0
    peers_sending_to_us: int = 0
    num_seeds: int = 0
    num_leechs: int = 0
    error: Optional[str] = None
    boost_state: str = "healthy"
    status_message: str = "Operating normally"
    servarr_app: Optional[str] = None
    servarr_title: Optional[str] = None
    servarr_queue_id: Optional[int] = None
    first_stalled_at: Optional[datetime] = None
    boosted_at: Optional[datetime] = None
    grace_period_expires_at: Optional[datetime] = None
    is_private: bool = False
    latest_judgement: Optional[Dict[str, Any]] = None

class BoostEventSummary(BaseModel):
    id: str
    timestamp: datetime
    torrent_id: str
    torrent_hash: str
    torrent_name: str
    action: str
    details: str
    servarr_app: Optional[str] = None
    success: bool = True

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

class WebUILink(BaseModel):
    name: str
    key: str
    url: str
    icon: str
    description: Optional[str] = None

class SystemStatus(BaseModel):
    daemon_running: bool
    current_location: Optional[str]
    active_torrents: int
    last_rotation: Optional[datetime]
    boost_enabled: bool = True
    auto_failover_enabled: bool = False
    llm_assisted_failover_enabled: bool = True
    auto_vpn_rotation_enabled: bool = True
    vpn_rotation_paused: bool = False
    cached_trackers_count: int = 0
    healthy_trackers_count: int = 0
    ollama_enabled: bool = True
    ollama_model: Optional[str] = None
    web_uis: List[WebUILink] = Field(default_factory=list)

class AutoFailoverToggleRequest(BaseModel):
    enabled: bool

class LLMAssistedFailoverToggleRequest(BaseModel):
    enabled: bool

class VpnRotationToggleRequest(BaseModel):
    enabled: Optional[bool] = None
    paused: Optional[bool] = None

class RotateRequest(BaseModel):
    location: Optional[str] = None

class JudgeTorrentRequest(BaseModel):
    user_prompt: Optional[str] = None
    force_fresh: bool = False

class LLMChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None

class LLMChatResponse(BaseModel):
    response: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)




