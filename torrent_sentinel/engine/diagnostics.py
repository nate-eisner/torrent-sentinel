import logging
from datetime import datetime
from typing import List, Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import TorrentInfo, OllamaDiagnosis

logger = logging.getLogger(__name__)

class Diagnostics:
    def __init__(self, transmission_client, ollama_client):
        self.transmission = transmission_client
        self.ollama = ollama_client

    async def analyze_torrents(self, torrents: List[TorrentInfo]) -> Optional[OllamaDiagnosis]:
        stalled_torrents = [
            {
                "id": t.id,
                "name": t.name,
                "error": t.error,
                "error_string": t.error_string,
                "peers_connected": t.peers_connected,
                "rate_download": t.rate_download
            }
            for t in torrents 
            if ((t.error is not None and t.error != 0 and str(t.error) != "0") or bool(t.error_string)) or (t.rate_download < settings.MIN_DOWNLOAD_RATE_KBPS and t.peers_connected < settings.MIN_SEEDS)
        ]

        if not stalled_torrents:
            return None

        context = {
            "stalled_torrents": stalled_torrents,
            "thresholds": {
                "min_seeds": settings.MIN_SEEDS,
                "min_rate_kbps": settings.MIN_DOWNLOAD_RATE_KBPS
            }
        }

        return await self.ollama.diagnose_stalled_torrents(context)
