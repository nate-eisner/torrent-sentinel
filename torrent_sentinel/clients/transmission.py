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

    def _normalize_id(self, identifier: str):
        return int(identifier) if identifier.isdigit() else identifier

    async def get_torrents(self) -> List[TorrentInfo]:
        logger.debug("Fetching torrent list from Transmission...")
        payload = {
            "method": "torrent-get",
            "arguments": {
                "fields": [
                    "id", "hashString", "name", "status", "percentDone",
                    "rateDownload", "rateUpload",
                    "peersConnected", "peersSendingToUs", "peersGettingFromUs",
                    "eta", "error", "errorString", "addedDate", "labels"
                ]
            }
        }

        async with httpx.AsyncClient() as client:
            data = await self._post_rpc(client, payload)

        raw_torrents = data.get("arguments", {}).get("torrents", [])
        torrents = []
        for t in raw_torrents:
            raw_labels = t.get("labels") or []
            labels = list(raw_labels) if isinstance(raw_labels, list) else [str(raw_labels)]
            torrents.append(TorrentInfo(
                id=str(t.get("id")),
                hash=str(t.get("hashString", "")).lower(),
                name=t.get("name", "Unknown"),
                status=str(t.get("status", "")),
                progress=float(t.get("percentDone", 0.0)),
                rateDownload=float(t.get("rateDownload", 0)),
                rateUpload=float(t.get("rateUpload", 0)),
                peersConnected=int(t.get("peersConnected", 0)),
                peersSendingToUs=int(t.get("peersSendingToUs", 0)),
                peersGettingFromUs=int(t.get("peersGettingFromUs", 0)),
                eta=t.get("eta"),
                error=t.get("error"),
                errorString=t.get("errorString"),
                addedDate=int(t.get("addedDate", 0)),
                labels=labels
            ))

        logger.info("Retrieved %d torrent(s) from Transmission", len(torrents))
        for t in torrents:
            logger.debug(
                "  - Torrent #%s '%s' (hash: %s, prog: %.1f%%) | status=%s | dl=%.1f KB/s, ul=%.1f KB/s | peers=%d (seeds=%d, leechs=%d) | error=%s ('%s')",
                t.id, t.name, t.hash[:8] if t.hash else "", t.progress * 100.0, t.status,
                t.rate_download / 1024.0, t.rate_upload / 1024.0,
                t.peers_connected, t.peers_sending_to_us, t.peers_getting_from_us,
                t.error, t.error_string or ""
            )

        return torrents

    async def add_trackers(self, torrent_id: str, trackers: List[str]) -> bool:
        """Inject a list of tracker URLs into a torrent swarm."""
        if not trackers:
            return True
        logger.info("Injecting %d tracker(s) into Transmission torrent %s", len(trackers), torrent_id)
        payload = {
            "method": "torrent-set",
            "arguments": {
                "ids": [self._normalize_id(torrent_id)],
                "trackerAdd": trackers
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await self._post_rpc(client, payload)
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to add trackers to torrent %s: %s", torrent_id, e)
            return False

    async def reannounce_torrents(self, torrent_ids: List[str]) -> bool:
        if not torrent_ids:
            return True
        logger.info("Requesting tracker re-announce for %d torrent(s): %s", len(torrent_ids), torrent_ids)
        payload = {
            "method": "torrent-reannounce",
            "arguments": {"ids": [self._normalize_id(i) for i in torrent_ids]}
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await self._post_rpc(client, payload)
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to reannounce torrents %s: %s", torrent_ids, e)
            return False

    async def recheck(self, torrent_id: str) -> bool:
        """Force verification of torrent integrity."""
        logger.info("Requesting force verification for torrent %s", torrent_id)
        payload = {
            "method": "torrent-verify",
            "arguments": {"ids": [self._normalize_id(torrent_id)]}
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await self._post_rpc(client, payload)
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to recheck torrent %s: %s", torrent_id, e)
            return False

    async def resume(self, torrent_id: str) -> bool:
        """Resume / start a torrent."""
        logger.info("Requesting start/resume for torrent %s", torrent_id)
        payload = {
            "method": "torrent-start",
            "arguments": {"ids": [self._normalize_id(torrent_id)]}
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await self._post_rpc(client, payload)
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to resume torrent %s: %s", torrent_id, e)
            return False

    async def delete_torrent(self, torrent_id: str, delete_files: bool = True) -> bool:
        """Delete torrent and optionally local downloaded files."""
        logger.warning("Deleting torrent %s (delete_files=%s)", torrent_id, delete_files)
        payload = {
            "method": "torrent-remove",
            "arguments": {
                "ids": [self._normalize_id(torrent_id)],
                "delete-local-data": delete_files
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await self._post_rpc(client, payload)
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to delete torrent %s: %s", torrent_id, e)
            return False

    async def add_labels(self, torrent_id: str, labels: List[str]) -> bool:
        """Add labels without overwriting existing ones."""
        if not labels:
            return True
        norm_id = self._normalize_id(torrent_id)
        try:
            async with httpx.AsyncClient() as client:
                data = await self._post_rpc(client, {
                    "method": "torrent-get",
                    "arguments": {"ids": [norm_id], "fields": ["labels"]}
                })
                existing_labels: List[str] = []
                torrents = data.get("arguments", {}).get("torrents", [])
                if torrents:
                    raw = torrents[0].get("labels") or []
                    if isinstance(raw, list):
                        existing_labels = list(raw)

                for label in labels:
                    if label not in existing_labels:
                        existing_labels.append(label)

                res = await self._post_rpc(client, {
                    "method": "torrent-set",
                    "arguments": {"ids": [norm_id], "labels": existing_labels}
                })
                return res.get("result") == "success"
        except Exception as e:
            logger.error("Failed to add labels %s to torrent %s: %s", labels, torrent_id, e)
            return False

