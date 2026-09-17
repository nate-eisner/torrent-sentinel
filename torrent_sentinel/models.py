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
