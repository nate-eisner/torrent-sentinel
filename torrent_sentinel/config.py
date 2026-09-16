from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Transmission
    TRANSMISSION_HOST: str = "localhost"
    TRANSMISSION_PORT: int = 9091
    TRANSMISSION_RPC_PATH: str = "/transmission/rpc"
    TRANSMISSION_AUTH: Optional[str] = None

    # Ollama
    OLLAMA_ENABLED: bool = True
    OLLAMA_BASE_URL: str = "http://192.168.0.10:11434"
    OLLAMA_MODEL: str = "gemma4:26b"
    OLLAMA_TIMEOUT: int = 300

    # VPN Provider (Unraid WireGuard)
    VPN_TYPE: str = "unraid_wireguard"  # unraid_wireguard, mock, command
    VPN_INTERFACE: str = "wg1"
    VPN_CONFIGS_DIR: str = "/mnt/user/appdata/torrent-sentinel/vpn_configs/"
    VPN_ACTIVE_CONFIG: str = "/mnt/user/appdata/torrent-sentinel/vpn_configs/active.conf"

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

    model_config = SettingsConfigDict(env_prefix="SENTINEL_", case_sensitive=False)

settings = Settings()
