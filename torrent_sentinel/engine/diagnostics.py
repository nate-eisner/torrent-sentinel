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
        if not torrents:
            logger.info("Diagnostics: No torrents currently managed in Transmission.")
            return None

        logger.debug("Diagnostics: Evaluating health of %d torrent(s)...", len(torrents))

        stalled_torrents = []
        for t in torrents:
            has_error = ((t.error is not None and t.error != 0 and str(t.error) != "0") or bool(t.error_string))
            is_slow = (t.rate_download < (settings.MIN_DOWNLOAD_RATE_KBPS * 1024.0) and t.peers_connected < settings.MIN_SEEDS)

            if has_error or is_slow:
                reason = "tracker/client error" if has_error else f"low peers ({t.peers_connected} < {settings.MIN_SEEDS}) and slow speed ({t.rate_download/1024:.1f} KB/s < {settings.MIN_DOWNLOAD_RATE_KBPS} KB/s)"
                logger.warning(
                    "Torrent #%s '%s' flagged as unhealthy: %s (error=%s, dl=%.1f KB/s, peers=%d)",
                    t.id, t.name, reason, t.error_string or t.error, t.rate_download / 1024.0, t.peers_connected
                )
                stalled_torrents.append({
                    "id": t.id,
                    "name": t.name,
                    "error": t.error,
                    "error_string": t.error_string,
                    "peers_connected": t.peers_connected,
                    "rate_download": t.rate_download
                })

        if not stalled_torrents:
            logger.info("Diagnostics: All %d torrent(s) are performing normally.", len(torrents))
            return None

        logger.warning(
            "Diagnostics identified %d unhealthy torrent(s) out of %d total.",
            len(stalled_torrents), len(torrents)
        )

        context = {
            "stalled_torrents": stalled_torrents,
            "thresholds": {
                "min_seeds": settings.MIN_SEEDS,
                "min_rate_kbps": settings.MIN_DOWNLOAD_RATE_KBPS
            }
        }

        return await self.ollama.diagnose_stalled_torrents(context)
