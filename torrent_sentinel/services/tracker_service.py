import logging
import asyncio
import struct
import random
import time
import urllib.parse
from datetime import datetime, timezone
from typing import List, Set, Dict, Optional, Tuple, Any
import httpx

from torrent_sentinel.config import settings
from torrent_sentinel.models import TrackerHealth

logger = logging.getLogger(__name__)

# Fallback top tier public trackers in case remote lists cannot be reached
DEFAULT_FALLBACK_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://opentracker.i2p.rocks:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "https://tracker.tamersunion.org:443/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://retracker.lanta-net.ru:2710/announce",
    "udp://tracker.zerobytes.xyz:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.theoks.net:6969/announce"
]


class _UDPTrackerProtocol(asyncio.DatagramProtocol):
    """Protocol to handle BEP 15 UDP connection handshake."""
    def __init__(self, transaction_id: int):
        self.transaction_id = transaction_id
        self.response_future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        # BEP 15 Connect response: 4 bytes action (0), 4 bytes transaction_id, 8 bytes connection_id
        if len(data) >= 16:
            try:
                action, trans_id = struct.unpack_from(">II", data, 0)
                if trans_id == self.transaction_id and action == 0:
                    if not self.response_future.done():
                        self.response_future.set_result(True)
                        return
            except Exception:
                pass
        if not self.response_future.done():
            self.response_future.set_result(False)

    def error_received(self, exc: Exception):
        if not self.response_future.done():
            self.response_future.set_exception(exc)

    def connection_lost(self, exc: Optional[Exception]):
        if not self.response_future.done():
            if exc:
                self.response_future.set_exception(exc)
            else:
                self.response_future.set_result(False)


async def probe_udp_tracker(host: str, port: int, timeout: float = 3.0) -> Tuple[bool, float, str]:
    """Test UDP tracker connectivity via BEP 15 connect handshake and measure latency."""
    loop = asyncio.get_running_loop()
    transaction_id = random.randint(0, 0x7FFFFFFF)
    t0 = time.monotonic()
    transport = None
    try:
        transport, protocol = await asyncio.wait_for(
            loop.create_datagram_endpoint(
                lambda: _UDPTrackerProtocol(transaction_id),
                remote_addr=(host, port)
            ),
            timeout=timeout
        )
        # BEP 15 connection request: protocol_id (0x41727101980), action 0 (connect), transaction_id
        packet = struct.pack(">QII", 0x41727101980, 0, transaction_id)
        transport.sendto(packet)
        success = await asyncio.wait_for(protocol.response_future, timeout=timeout)
        latency = round((time.monotonic() - t0) * 1000.0, 1)
        if success:
            return True, latency, "Online"
        else:
            return False, latency, "Invalid handshake response"
    except asyncio.TimeoutError:
        return False, 0.0, "Timed out"
    except Exception as e:
        return False, 0.0, f"Unreachable ({e})"
    finally:
        if transport:
            transport.close()


async def probe_http_tracker(url: str, timeout: float = 3.0) -> Tuple[bool, float, str]:
    """Test HTTP/HTTPS tracker connectivity and measure latency."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True) as client:
            resp = await client.head(url)
            latency = round((time.monotonic() - t0) * 1000.0, 1)
            return True, latency, f"HTTP {resp.status_code}"
    except httpx.HTTPStatusError as e:
        latency = round((time.monotonic() - t0) * 1000.0, 1)
        return True, latency, f"HTTP {e.response.status_code}"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return False, 0.0, type(e).__name__
    except Exception as e:
        return False, 0.0, f"Error: {e}"


class TrackerService:
    def __init__(self, urls: List[str]):
        self.urls = urls
        self.cached_trackers: List[str] = list(DEFAULT_FALLBACK_TRACKERS)
        self.tracker_health: Dict[str, TrackerHealth] = {
            u: TrackerHealth(url=u, is_alive=True, status="Initialized")
            for u in self.cached_trackers
        }
        self.last_refreshed: Optional[datetime] = None
        self.last_probed: Optional[datetime] = None

    def should_refresh(self, interval_hours: int) -> bool:
        """Check if periodic tracker list refresh is due."""
        if interval_hours <= 0:
            return False
        if self.last_refreshed is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_refreshed).total_seconds()
        return elapsed >= (interval_hours * 3600)

    async def probe_tracker(self, url: str, timeout: float = 3.0) -> TrackerHealth:
        """Probe single tracker endpoint for reachability and latency."""
        existing = self.tracker_health.get(url)
        peers_seen = existing.peers_seen if existing else 0

        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme == "udp":
            if not parsed.hostname or not parsed.port:
                health = TrackerHealth(
                    url=url, is_alive=False, latency_ms=None,
                    last_checked=datetime.now(timezone.utc),
                    status="Invalid UDP URL", peers_seen=peers_seen
                )
            else:
                is_alive, latency, status = await probe_udp_tracker(parsed.hostname, parsed.port, timeout=timeout)
                health = TrackerHealth(
                    url=url, is_alive=is_alive, latency_ms=latency if is_alive else None,
                    last_checked=datetime.now(timezone.utc),
                    status=status, peers_seen=peers_seen
                )
        elif scheme in ("http", "https"):
            is_alive, latency, status = await probe_http_tracker(url, timeout=timeout)
            health = TrackerHealth(
                url=url, is_alive=is_alive, latency_ms=latency if is_alive else None,
                last_checked=datetime.now(timezone.utc),
                status=status, peers_seen=peers_seen
            )
        else:
            # Fallback for websocket / other protocols
            health = TrackerHealth(
                url=url, is_alive=True, latency_ms=None,
                last_checked=datetime.now(timezone.utc),
                status="Unprobed protocol", peers_seen=peers_seen
            )

        self.tracker_health[url] = health
        return health

    async def probe_all_trackers(
        self,
        urls: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        max_concurrent: Optional[int] = None
    ) -> Dict[str, TrackerHealth]:
        """Probe all cached or specified trackers concurrently with a concurrency limit."""
        target_urls = urls if urls is not None else list(self.cached_trackers)
        if not target_urls:
            return {}

        probe_timeout = timeout if timeout is not None else settings.TRACKER_PROBE_TIMEOUT_SECONDS
        concurrency = max_concurrent if max_concurrent is not None else settings.TRACKER_MAX_CONCURRENT_PROBES
        semaphore = asyncio.Semaphore(concurrency)

        async def _probe_with_sem(u: str) -> TrackerHealth:
            async with semaphore:
                return await self.probe_tracker(u, timeout=probe_timeout)

        logger.info("Probing %d tracker endpoints (timeout=%.1fs, concurrency=%d)...", len(target_urls), probe_timeout, concurrency)
        tasks = [_probe_with_sem(u) for u in target_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        healthy_count = 0
        for r in results:
            if isinstance(r, TrackerHealth):
                self.tracker_health[r.url] = r
                if r.is_alive:
                    healthy_count += 1

        self.last_probed = datetime.now(timezone.utc)
        logger.info("Tracker probe completed: %d/%d responsive", healthy_count, len(target_urls))
        return self.tracker_health

    def record_transmission_stats(self, tracker_stats: List[Dict[str, Any]]):
        """Update tracker health with real-world announce results observed by Transmission."""
        if not tracker_stats:
            return

        for stat in tracker_stats:
            announce = stat.get("announce")
            if not announce:
                continue

            health = self.tracker_health.get(announce)
            if not health:
                health = TrackerHealth(url=announce, is_alive=True, status="Discovered from Transmission")
                self.tracker_health[announce] = health

            peer_count = stat.get("lastAnnouncePeerCount", 0)
            if peer_count > health.peers_seen:
                health.peers_seen = peer_count

            if stat.get("lastAnnounceSucceeded"):
                health.is_alive = True
                health.status = stat.get("lastAnnounceResult") or "Success"
            elif stat.get("lastAnnounceResult"):
                health.status = stat.get("lastAnnounceResult")

    async def refresh_trackers(self, probe: Optional[bool] = None) -> List[str]:
        """Fetch fresh trackers from configured endpoints, update cache, and probe health."""
        discovered: Set[str] = set()

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for url in self.urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        lines = resp.text.splitlines()
                        for line in lines:
                            cleaned = line.strip()
                            if cleaned and (
                                cleaned.startswith("udp://") or
                                cleaned.startswith("http://") or
                                cleaned.startswith("https://") or
                                cleaned.startswith("wss://")
                            ):
                                discovered.add(cleaned)
                    else:
                        logger.warning("Failed to fetch tracker list from %s: status %d", url, resp.status_code)
                except Exception as e:
                    logger.warning("Error fetching tracker list from %s: %s", url, e)

        if discovered:
            self.cached_trackers = sorted(list(discovered))
            logger.info("Refreshed tracker cache: %d discovered trackers from remote sources", len(self.cached_trackers))
        else:
            logger.info("Remote lists unavailable; using %d default fallback trackers", len(self.cached_trackers))

        self.last_refreshed = datetime.now(timezone.utc)

        # Initialize health records for newly discovered trackers
        for u in self.cached_trackers:
            if u not in self.tracker_health:
                self.tracker_health[u] = TrackerHealth(url=u, is_alive=True, status="Discovered")

        # Optionally probe health
        should_probe = probe if probe is not None else settings.TRACKER_PROBE_ENABLED
        if should_probe:
            try:
                await self.probe_all_trackers()
            except Exception as e:
                logger.warning("Tracker health probe failed: %s", e)

        return list(self.cached_trackers)

    def get_trackers(self, only_healthy: bool = True) -> List[str]:
        """Return cached trackers. If only_healthy=True, returns verified responsive trackers sorted by lowest latency."""
        if not only_healthy:
            return list(self.cached_trackers)

        healthy = [
            u for u in self.cached_trackers
            if self.tracker_health.get(u) and self.tracker_health[u].is_alive
        ]

        if not healthy:
            # Fallback to all cached trackers if none passed probing or probing hasn't finished
            return list(self.cached_trackers)

        # Sort by latency: trackers with measured latency first (lowest to highest), then unmeasured
        def _sort_key(u: str) -> float:
            h = self.tracker_health.get(u)
            if h and h.latency_ms is not None and h.latency_ms > 0:
                return h.latency_ms
            return 999999.0

        return sorted(healthy, key=_sort_key)

    def get_health_summary(self) -> Dict[str, Any]:
        """Return structured summary of tracker health and reachability."""
        healthy_trackers = [h for h in self.tracker_health.values() if h.is_alive]
        return {
            "total_discovered": len(self.cached_trackers),
            "healthy_count": len(healthy_trackers),
            "last_refreshed": self.last_refreshed.isoformat() if self.last_refreshed else None,
            "last_probed": self.last_probed.isoformat() if self.last_probed else None,
            "trackers": [h.model_dump() for h in self.tracker_health.values()]
        }
