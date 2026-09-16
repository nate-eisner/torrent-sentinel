import logging
import httpx
from typing import List, Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import TorrentInfo, TrackerInfo

logger = logging.getLogger(__name__)

class TransmissionClient:
    def __init__(self):
        self.base_url = f"http://{settings.TRANSMISSION_HOST}:{settings.TRANSMISSION_PORT}{settings.TRANSMISSION_RPC_PATH}"
        self.session_id = None
        self._auth = None
        if settings.TRANSMISSION_AUTH and ":" in settings.TRANSMISSION_AUTH:
            user, pwd = settings.TRANSMISSION_AUTH.split(":", 1)
            self._auth = (user, pwd)

    async def _post_rpc(self, client: httpx.AsyncClient, payload: dict) -> dict:
        headers = {}
        if self.session_id:
            headers["X-Transmission-Session-Id"] = self.session_id

        method = payload.get("method", "unknown")
        logger.debug("Posting RPC request to Transmission [%s]: method=%s", self.base_url, method)

        try:
            response = await client.post(
                self.base_url,
                json=payload,
                headers=headers,
                auth=self._auth,
                timeout=15.0
            )
        except httpx.ConnectError as e:
            logger.error(
                "Unable to connect to Transmission RPC at %s. Please verify SENTINEL_TRANSMISSION_HOST and SENTINEL_TRANSMISSION_PORT. Error: %s",
                self.base_url, e
            )
            raise
        except httpx.TimeoutException as e:
            logger.error("Transmission RPC request timed out at %s after 15 seconds. Error: %s", self.base_url, e)
            raise
        except Exception as e:
            logger.error("Unexpected error connecting to Transmission at %s: %s", self.base_url, e, exc_info=True)
            raise

        # Transmission returns 409 Conflict with the initial or refreshed session ID
        if response.status_code == 409:
            self.session_id = response.headers.get("X-Transmission-Session-Id")
            logger.debug("Received new Transmission session token: %s. Retrying RPC call...", self.session_id)
            headers["X-Transmission-Session-Id"] = self.session_id
            response = await client.post(
                self.base_url,
                json=payload,
                headers=headers,
                auth=self._auth,
                timeout=15.0
            )

        if response.status_code == 401:
            logger.error("Transmission returned HTTP 401 Unauthorized. Please check SENTINEL_TRANSMISSION_AUTH credentials.")

        response.raise_for_status()
        return response.json()

    async def get_torrents(self) -> List[TorrentInfo]:
        logger.debug("Fetching torrent list from Transmission...")
        payload = {
            "method": "torrent-get",
            "arguments": {
                "fields": [
                    "id", "name", "status", "percentDone",
                    "rateDownload", "rateUpload",
                    "downloadSpeed", "uploadSpeed",
                    "peersConnected", "peersSendingToUs",
                    "eta", "error", "errorString"
                ]
            }
        }

        async with httpx.AsyncClient() as client:
            data = await self._post_rpc(client, payload)

        raw_torrents = data.get("arguments", {}).get("torrents", [])
        torrents = []
        for t in raw_torrents:
            torrents.append(TorrentInfo(
                id=str(t.get("id")),
                name=t.get("name", "Unknown"),
                status=str(t.get("status", "")),
                rateDownload=float(t.get("rateDownload", t.get("downloadSpeed", 0))),
                rateUpload=float(t.get("rateUpload", t.get("uploadSpeed", 0))),
                peersConnected=int(t.get("peersConnected", 0)),
                peersSendingToUs=int(t.get("peersSendingToUs", 0)),
                eta=t.get("eta"),
                error=t.get("error"),
                errorString=t.get("errorString")
            ))

        logger.info("Retrieved %d torrent(s) from Transmission", len(torrents))
        for t in torrents:
            logger.debug(
                "  - Torrent #%s '%s' | status=%s | dl=%.1f KB/s, ul=%.1f KB/s | peers=%d (active=%d) | error=%s ('%s')",
                t.id, t.name, t.status, t.rate_download / 1024.0, t.rate_upload / 1024.0,
                t.peers_connected, t.peers_sending_to_us, t.error, t.error_string or ""
            )

        return torrents

    async def reannounce_torrents(self, torrent_ids: List[str]):
        logger.info("Requesting tracker re-announce for %d torrent(s): %s", len(torrent_ids), torrent_ids)
        payload = {
            "method": "torrent-reannounce",
            "arguments": {"ids": [int(i) for i in torrent_ids]}
        }
        async with httpx.AsyncClient() as client:
            await self._post_rpc(client, payload)
        logger.info("Successfully sent reannounce signal to Transmission")
