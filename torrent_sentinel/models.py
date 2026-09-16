from pydantic import BaseModel, Field
from typing import Optional, List, Union
from datetime import datetime

class TorrentInfo(BaseModel):
    id: str
    name: str
    status: str
    rate_download: float = Field(alias="rateDownload")
    rate_upload: float = Field(alias="rateUpload")
    peers_connected: int = Field(alias="peersConnected")
    peers_sending_to_us: int = Field(alias="peersSendingToUs")
    eta: Optional[int] = None
    error: Optional[Union[int, str]] = None
    error_string: Optional[str] = Field(None, alias="errorString")

class TrackerInfo(BaseModel):
    announce_url: str
    status: str
    last_announce_result: Optional[str] = None
    last_announce_succeeded: bool
    last_announce_peer_count: int

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
