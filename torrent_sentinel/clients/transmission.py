import httpx
from typing import List, Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import TorrentInfo, TrackerInfo

class TransmissionClient:
    def __init__(self):
        self.base_url = f"http://{settings.TRANSMISSION_HOST}:{settings.TRANSMISSION_PORT}{settings.TRANSMISSION_RPC_PATH}"
        self.session_id = None

    async def _authenticate(self, client: httpx.AsyncClient):
        try:
            response = await client.get(self.base_url)
            self.session_id = response.headers.get("X-Transmission-Session-Id")
        except Exception:
            self.session_id = None

    async def get_torrents(self) -> List[TorrentInfo]:
        async with httpx.AsyncClient() as client:
            await self._authenticate(client)
            headers = {"X-Transmission-Session-Id": self.session_id} if self.session_id else {}
            
            payload = {
                "method": "torrent-get",
                "arguments": {"fields": ["id", "name", "status", "percentDone", "downloadSpeed", "uploadSpeed", "peersConnected", "peersSendingToUs", "eta", "error", "errorString"]}
            }
            
            response = await client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            torrents = []
            for t in data["arguments"]["torrents"]:
                torrents.append(TorrentInfo(
                    id=str(t["id"]),
                    name=t["name"],
                    status=str(t["status"]),
                    rateDownload=float(t.get("downloadSpeed", 0)),
                    rateUpload=float(t.get("uploadSpeed", 0)),
                    peersConnected=int(t.get("peersConnected", 0)),
                    peersSendingToUs=int(t.get("peersSendingToUs", 0)),
                    eta=t.get("eta"),
                    error=t.get("error"),
                    errorString=t.get("errorString")
                ))
            return torrents

    async def reannounce_torrents(self, torrent_ids: List[str]):
        async with httpx.AsyncClient() as client:
            await self._authenticate(client)
            headers = {"X-Transmission-Session-Id": self.session_id} if self.session_id else {}
            
            payload = {
                "method": "torrent-reannounce",
                "arguments": {"ids": [int(i) for i in torrent_ids]}
            }
            
            response = await client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
