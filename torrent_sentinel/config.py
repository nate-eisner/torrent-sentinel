from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Transmission
    TRANSMISSION_HOST: str = "127.0.0.1"
    TRANSMISSION_PORT: int = 9091
    TRANSMISSION_RPC_PATH: str = "/transmission/rpc"
    TRANSMISSION_AUTH: Optional[str] = None

    # Ollama
    OLLAMA_ENABLED: bool = True
    OLLAMA_BASE_URL: str = "http://192.168.0.10:11434"
    OLLAMA_MODEL: str = "gemma4:26b"
    OLLAMA_TIMEOUT: int = 300

    # VPN Gateway (WireGuard)
    VPN_TYPE: str = "wireguard"  # wireguard, unraid_wireguard, mock
    VPN_INTERFACE: str = "wg1"
    VPN_CONFIGS_DIR: str = "/app/vpn_configs/"
    VPN_ACTIVE_CONFIG: str = "/app/vpn_configs/active.conf"

    # Local Network Subnets for LAN WebUI Bypass (comma-separated CIDRs)
    LAN_NETWORK: Optional[str] = "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"

    # Health Thresholds
    MIN_SEEDS: int = 2
    MIN_DOWNLOAD_RATE_KBPS: float = 15.0
    STALLED_DURATION_MINUTES: int = 5

    # Anti-Flapping
    ROTATION_COOLDOWN_MINUTES: int = 15
    POST_ROTATION_GRACE_PERIOD_MINUTES: int = 3
    MAX_ROTATIONS_PER_HOUR: int = 4

    # Notifications
    DISCORD_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Boosting & Rescue
    BOOST_ENABLED: bool = True
    AUTO_VPN_ROTATION_ENABLED: bool = True
    STALL_THRESHOLD_MINUTES: int = 5
    RESCUE_GRACE_PERIOD_MINUTES: int = 60
    AUTO_FAILOVER_ENABLED: bool = False
    AUTO_BOOST_CADENCE_MINUTES: int = 120
    TRACKER_LIST_URLS: list[str] = [
        "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
        "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_all_udp.txt",
        "https://newtrackon.com/api/stable"
    ]

    # Servarr Settings (Sonarr, Radarr, Lidarr)
    SONARR_URL: Optional[str] = None
    SONARR_API_KEY: Optional[str] = None
    RADARR_URL: Optional[str] = None
    RADARR_API_KEY: Optional[str] = None
    LIDARR_URL: Optional[str] = None
    LIDARR_API_KEY: Optional[str] = None

    # Logging
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # Web Dashboard / API Server
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000

    model_config = SettingsConfigDict(env_prefix="SENTINEL_", case_sensitive=False)

settings = Settings()
