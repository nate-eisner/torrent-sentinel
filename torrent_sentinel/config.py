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

    # Logging
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # Web Dashboard / API Server
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000

    model_config = SettingsConfigDict(env_prefix="SENTINEL_", case_sensitive=False)

settings = Settings()
