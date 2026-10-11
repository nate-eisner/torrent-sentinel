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
    is_off_vpn: bool = False
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
    vpn_enabled: bool = True
    vpn_connected: bool = True
    off_vpn_mode: bool = False
    off_vpn_torrent_id: Optional[str] = None
    off_vpn_torrent_name: Optional[str] = None
    off_vpn_paused_count: int = 0
    cached_trackers_count: int = 0
    healthy_trackers_count: int = 0
    ollama_enabled: bool = True
    ollama_model: Optional[str] = None
    autopilot_mode: str = "off"
    web_uis: List[WebUILink] = Field(default_factory=list)

class VpnToggleRequest(BaseModel):
    enabled: Optional[bool] = None
    location: Optional[str] = None

class OffVpnStartRequest(BaseModel):
    auto_restore: bool = True

class OffVpnStatusResponse(BaseModel):
    is_active: bool
    torrent_id: Optional[str] = None
    torrent_hash: Optional[str] = None
    torrent_name: Optional[str] = None
    paused_count: int = 0
    paused_torrent_ids: List[str] = Field(default_factory=list)
    previous_vpn_location: Optional[str] = None
    started_at: Optional[datetime] = None
    auto_restore_on_complete: bool = True

class VpnStatusResponse(BaseModel):
    status: str = "success"
    vpn_enabled: bool
    connected: bool
    current_location: Optional[str] = None
    off_vpn_mode: bool = False
    off_vpn_torrent_name: Optional[str] = None

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

class AutopilotModeToggleRequest(BaseModel):
    mode: str  # off, advisory, full

class AutopilotTriggerRequest(BaseModel):
    force: bool = True
    mode: Optional[str] = None

class AutopilotStatusResponse(BaseModel):
    mode: str
    last_run: Optional[datetime] = None
    last_plan: Optional[Dict[str, Any]] = None
    recent_events: List[Dict[str, Any]] = Field(default_factory=list)

class ModelSelectRequest(BaseModel):
    model: str

class AvailableModelsResponse(BaseModel):
    current_model: str
    available_models: List[str] = Field(default_factory=list)
