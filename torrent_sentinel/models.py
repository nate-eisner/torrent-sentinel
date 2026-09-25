import uuid
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Union, Dict, Any
from datetime import datetime, timezone

class BoostState(str, Enum):
    HEALTHY = "healthy"
    STALLED = "stalled"
    BOOSTING = "boosting"
    REVIVED = "revived"
    PROBATION_EXPIRED = "probation_expired"
    FAILED_OVER = "failed_over"
    COMPLETED = "completed"
    ERROR = "error"

class ServarrType(str, Enum):
    SONARR = "sonarr"
    RADARR = "radarr"
    LIDARR = "lidarr"

class TorrentInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    status: str
    hash: str = Field(default="", alias="hashString")
    progress: float = Field(default=0.0, alias="percentDone")
    rate_download: float = Field(default=0.0, alias="rateDownload")
    rate_upload: float = Field(default=0.0, alias="rateUpload")
    peers_connected: int = Field(default=0, alias="peersConnected")
    peers_sending_to_us: int = Field(default=0, alias="peersSendingToUs")
    peers_getting_from_us: int = Field(default=0, alias="peersGettingFromUs")
    eta: Optional[int] = None
    error: Optional[Union[int, str]] = None
    error_string: Optional[str] = Field(None, alias="errorString")
    added_on: int = Field(default=0, alias="addedDate")
    labels: List[str] = Field(default_factory=list)
    is_private: bool = Field(default=False, alias="isPrivate")
    tracker_stats: List[Dict[str, Any]] = Field(default_factory=list, alias="trackerStats")

class TrackerInfo(BaseModel):
    announce_url: str
    status: str
    last_announce_result: Optional[str] = None
    last_announce_succeeded: bool
    last_announce_peer_count: int

class TrackerHealth(BaseModel):
    url: str
    is_alive: bool = True
    latency_ms: Optional[float] = None
    last_checked: Optional[datetime] = None
    status: str = "Unknown"
    peers_seen: int = 0

class ServarrQueueItem(BaseModel):
    id: int
    app: ServarrType
    title: str
    download_id: str
    series_title: Optional[str] = None
    episode_title: Optional[str] = None
    movie_title: Optional[str] = None
    artist_title: Optional[str] = None
    album_title: Optional[str] = None
    status: str
    tracked_download_state: Optional[str] = None
    error_message: Optional[str] = None

class BoostEvent(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    torrent_id: str
    torrent_hash: str
    torrent_name: str
    action: str
    details: str
    servarr_app: Optional[ServarrType] = None
    success: bool = True

class TorrentVerdict(str, Enum):
    HEALTHY = "healthy"
    SLOW_PROGRESS = "slow_progress"
    STALLED_WAITING = "stalled_waiting"
    NEEDS_BOOST = "needs_boost"
    NEEDS_RECHECK = "needs_recheck"
    VPN_THROTTLED = "vpn_throttled"
    DEAD_SWARM = "dead_swarm"
    CLIENT_ERROR = "client_error"

class RecommendedAction(str, Enum):
    WAIT = "wait"
    BOOST_TRACKERS = "boost_trackers"
    RECHECK = "recheck"
    REANNOUNCE = "reannounce"
    ROTATE_VPN = "rotate_vpn"
    FAILOVER = "failover"
    MANUAL_ACTION = "manual_action"

class TorrentJudgement(BaseModel):
    id: str = Field(default_factory=lambda: str(datetime.now(timezone.utc).timestamp()))
    torrent_id: str
    torrent_hash: str
    torrent_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verdict: TorrentVerdict
    viability_score: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_action: RecommendedAction
    action_explanation: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str
    tracker_analysis: Optional[str] = None
    user_prompt: Optional[str] = None

class SwarmAssessment(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    overall_summary: str
    vpn_health_verdict: str
    should_rotate_vpn: bool
    vpn_reasoning: str
    torrent_judgements: List[TorrentJudgement] = Field(default_factory=list)
    recommended_actions: List[Dict[str, Any]] = Field(default_factory=list)

class UnifiedTorrentItem(BaseModel):
    id: str
    hash: str
    name: str
    status: str
    progress: float
    rate_download: float
    rate_upload: float
    peers_connected: int
    peers_sending_to_us: int
    num_seeds: int
    num_leechs: int
    eta: Optional[int] = None
    error: Optional[Union[int, str]] = None
    error_string: Optional[str] = None
    boost_state: BoostState = BoostState.HEALTHY
    status_message: str = "Operating normally"
    first_stalled_at: Optional[datetime] = None
    boosted_at: Optional[datetime] = None
    grace_period_expires_at: Optional[datetime] = None
    servarr_app: Optional[ServarrType] = None
    servarr_title: Optional[str] = None
    servarr_queue_id: Optional[int] = None
    is_errored: bool = False
    is_private: bool = False
    latest_judgement: Optional[TorrentJudgement] = None

class OllamaDiagnosis(BaseModel):
    should_rotate: bool
    recommended_location: Optional[str] = None
    confidence: float
    reasoning: str
    suggested_action: str

class LocationProfile(BaseModel):
    id: str
    name: str
    country: str
    endpoint: str
    config_file: str

class RotationEvent(BaseModel):
    id: str
    timestamp: datetime
    from_location: str
    to_location: str
    reason: str
    peers_before: int
    peers_after: int

class AutopilotMode(str, Enum):
    OFF = "off"
    ADVISORY = "advisory"
    FULL = "full"

class AutopilotAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    action_type: RecommendedAction
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    viability_score: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    executed: bool = False
    execution_result: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AutopilotPlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: AutopilotMode = AutopilotMode.OFF
    summary: str
    vpn_health_verdict: str = "healthy"
    should_rotate_vpn: bool = False
    vpn_reasoning: str = ""
    preferred_vpn_location: Optional[str] = None
    actions: List[AutopilotAction] = Field(default_factory=list)
    guardrails_applied: List[str] = Field(default_factory=list)

class AutopilotEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    plan_id: Optional[str] = None
    mode: str
    action_type: str
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    confidence: float = 0.0
    viability_score: float = 0.0
    reasoning: str
    executed: bool = False
    execution_result: Optional[str] = None

