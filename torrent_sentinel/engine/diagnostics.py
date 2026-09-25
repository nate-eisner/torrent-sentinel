import logging
from datetime import datetime
from typing import List, Optional, Any, Dict
from torrent_sentinel.config import settings
from torrent_sentinel.models import TorrentInfo, OllamaDiagnosis, TorrentJudgement, SwarmAssessment


logger = logging.getLogger(__name__)

class Diagnostics:
    def __init__(self, transmission_client, ollama_client):
        self.transmission = transmission_client
        self.ollama = ollama_client

    async def analyze_torrents(
        self,
        torrents: List[TorrentInfo],
        stalled_records: Optional[dict] = None
    ) -> Optional[OllamaDiagnosis]:
        if not torrents:
            logger.info("Diagnostics: No torrents currently managed in Transmission.")
            return None

        logger.debug("Diagnostics: Evaluating health of %d torrent(s)...", len(torrents))

        stalled_torrents = []
        downloading_torrents = []
        completed_count = 0

        min_rate_bytes = settings.MIN_DOWNLOAD_RATE_KBPS * 1024.0

        for t in torrents:
            is_complete = t.progress >= 1.0 or t.status.lower() in ("seed", "seeding", "uploading")
            if is_complete:
                completed_count += 1
                continue

            has_error = ((t.error is not None and t.error != 0 and str(t.error) != "0") or bool(t.error_string))
            is_slow_or_low_peers = (t.rate_download < min_rate_bytes and t.peers_connected < settings.MIN_SEEDS)

            t_key = t.hash.lower() if t.hash else t.id
            rec = stalled_records.get(t_key) if stalled_records else None
            boost_state = rec.state.value if rec else "normal"
            boost_count = rec.boost_count if rec else 0

            if has_error or is_slow_or_low_peers:
                stalled_torrents.append({
                    "id": t.id,
                    "name": t.name,
                    "progress_pct": round(t.progress * 100.0, 1),
                    "error": t.error,
                    "error_string": t.error_string,
                    "peers_connected": t.peers_connected,
                    "peers_sending_to_us": t.peers_sending_to_us,
                    "rate_download_kbps": round(t.rate_download / 1024.0, 1),
                    "boost_state": boost_state,
                    "boost_count": boost_count
                })
            else:
                downloading_torrents.append({
                    "id": t.id,
                    "name": t.name,
                    "progress_pct": round(t.progress * 100.0, 1),
                    "peers_connected": t.peers_connected,
                    "peers_sending_to_us": t.peers_sending_to_us,
                    "rate_download_kbps": round(t.rate_download / 1024.0, 1)
                })

        if not stalled_torrents:
            logger.info("Diagnostics: All %d torrent(s) are performing normally.", len(torrents))
            return None

        total_downloading_peers = sum(d["peers_connected"] for d in downloading_torrents)
        avg_downloading_peers = (total_downloading_peers / len(downloading_torrents)) if downloading_torrents else 0.0

        logger.info(
            "Diagnostics identified %d stalled torrent(s) and %d downloading torrent(s) (avg peers on downloading: %.1f).",
            len(stalled_torrents), len(downloading_torrents), avg_downloading_peers
        )

        context = {
            "summary": {
                "total_torrents": len(torrents),
                "stalled_count": len(stalled_torrents),
                "downloading_count": len(downloading_torrents),
                "completed_count": completed_count,
                "average_downloading_peers": round(avg_downloading_peers, 1),
                "total_downloading_peers": total_downloading_peers
            },
            "stalled_torrents": stalled_torrents,
            "downloading_torrents": downloading_torrents,
            "thresholds": {
                "min_seeds": settings.MIN_SEEDS,
                "min_rate_kbps": settings.MIN_DOWNLOAD_RATE_KBPS
            }
        }

        return await self.ollama.diagnose_stalled_torrents(context)

    def build_torrent_context(
        self,
        t: TorrentInfo,
        stalled_rec: Optional[Any] = None,
        servarr_match: Optional[Any] = None,
        vpn_info: Optional[dict] = None
    ) -> dict:
        tracker_summary = []
        if getattr(t, "tracker_stats", None):
            for ts in t.tracker_stats:
                tracker_summary.append({
                    "announce": ts.get("announce", ""),
                    "host": ts.get("host", ""),
                    "last_result": ts.get("lastAnnounceResult", ""),
                    "last_succeeded": ts.get("lastAnnounceSucceeded", False),
                    "reported_seeders": ts.get("seederCount", -1),
                    "reported_leechers": ts.get("leecherCount", -1),
                    "last_announce_peer_count": ts.get("lastAnnouncePeerCount", 0)
                })

        servarr_info = None
        if servarr_match:
            q_item = servarr_match[0] if isinstance(servarr_match, tuple) else servarr_match
            servarr_info = {
                "app": q_item.app.value if hasattr(q_item, "app") and q_item.app else None,
                "title": getattr(q_item, "title", None),
                "media_title": getattr(q_item, "series_title", None) or getattr(q_item, "movie_title", None) or getattr(q_item, "album_title", None),
                "status": getattr(q_item, "status", None),
                "tracked_state": getattr(q_item, "tracked_download_state", None)
            }

        boost_state = stalled_rec.state.value if stalled_rec and hasattr(stalled_rec, "state") else "normal"
        boost_count = getattr(stalled_rec, "boost_count", 0) if stalled_rec else 0
        stalled_since = stalled_rec.first_stalled_at.isoformat() if stalled_rec and getattr(stalled_rec, "first_stalled_at", None) else None

        return {
            "id": t.id,
            "hash": t.hash,
            "name": t.name,
            "status": t.status,
            "progress_percent": round(t.progress * 100.0, 2),
            "rate_download_kbps": round(t.rate_download / 1024.0, 2),
            "rate_upload_kbps": round(t.rate_upload / 1024.0, 2),
            "peers_connected": t.peers_connected,
            "peers_sending_to_us": t.peers_sending_to_us,
            "peers_getting_from_us": t.peers_getting_from_us,
            "eta_seconds": t.eta,
            "error_code": t.error,
            "error_string": t.error_string,
            "is_private": getattr(t, "is_private", False),
            "boost_state": boost_state,
            "boost_count": boost_count,
            "stalled_since": stalled_since,
            "trackers": tracker_summary,
            "servarr": servarr_info,
            "vpn_context": vpn_info or {}
        }

    async def judge_torrent(
        self,
        torrent: TorrentInfo,
        stalled_rec: Optional[Any] = None,
        servarr_match: Optional[Any] = None,
        vpn_info: Optional[dict] = None,
        user_prompt: Optional[str] = None
    ):
        context = self.build_torrent_context(torrent, stalled_rec, servarr_match, vpn_info)
        return await self.ollama.judge_single_torrent(context, user_prompt=user_prompt)

    async def assess_swarm(
        self,
        torrents: List[TorrentInfo],
        stalled_records: Optional[dict] = None,
        vpn_info: Optional[dict] = None
    ):
        torrents_data = []
        for t in torrents:
            t_key = t.hash.lower() if t.hash else t.id
            rec = stalled_records.get(t_key) if stalled_records else None
            torrents_data.append(self.build_torrent_context(t, stalled_rec=rec, vpn_info=vpn_info))

        return await self.ollama.assess_entire_swarm(torrents_data, vpn_context=vpn_info or {})


