import pytest
import httpx
from unittest.mock import patch, AsyncMock
from torrent_sentinel.services.tracker_service import TrackerService, DEFAULT_FALLBACK_TRACKERS

def test_tracker_service_defaults():
    svc = TrackerService(urls=[])
    trackers = svc.get_trackers()
    assert len(trackers) == len(DEFAULT_FALLBACK_TRACKERS)
    assert "udp://tracker.opentrackr.org:1337/announce" in trackers

@pytest.mark.asyncio
async def test_tracker_service_refresh_success():
    svc = TrackerService(urls=["https://example.com/trackers.txt"])
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = (
        "udp://tracker.custom1.org:6969/announce\n"
        "\n"
        "# Comment line\n"
        "https://tracker.custom2.com:443/announce\n"
        "invalid-line\n"
    )

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        trackers = await svc.refresh_trackers()
        assert len(trackers) == 2
        assert "udp://tracker.custom1.org:6969/announce" in trackers
        assert "https://tracker.custom2.com:443/announce" in trackers

@pytest.mark.asyncio
async def test_tracker_service_refresh_failure_falls_back():
    svc = TrackerService(urls=["https://invalid.example.com/trackers.txt"])
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectError("Refused"))):
        trackers = await svc.refresh_trackers()
        assert len(trackers) == len(DEFAULT_FALLBACK_TRACKERS)

@pytest.mark.asyncio
async def test_probe_udp_tracker_mocked():
    from torrent_sentinel.services.tracker_service import probe_udp_tracker
    from unittest.mock import MagicMock
    with patch("asyncio.wait_for") as mock_wait:
        mock_transport = MagicMock()
        mock_protocol = MagicMock()
        mock_protocol.response_future = AsyncMock()
        mock_wait.side_effect = [
            (mock_transport, mock_protocol), # create_datagram_endpoint
            True                             # protocol.response_future
        ]
        alive, latency, status = await probe_udp_tracker("tracker.example.org", 1337)
        assert alive is True
        assert status == "Online"
        assert latency >= 0.0

@pytest.mark.asyncio
async def test_probe_udp_tracker_timeout():
    from torrent_sentinel.services.tracker_service import probe_udp_tracker
    import asyncio
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        alive, latency, status = await probe_udp_tracker("tracker.example.org", 1337)
        assert alive is False
        assert status == "Timed out"

@pytest.mark.asyncio
async def test_probe_http_tracker():
    from torrent_sentinel.services.tracker_service import probe_http_tracker
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.head", AsyncMock(return_value=mock_resp)):
        alive, latency, status = await probe_http_tracker("http://tracker.example.com/announce")
        assert alive is True
        assert status == "HTTP 200"

    # HTTP 400 is also considered reachable (trackers reject requests without info_hash)
    mock_resp_400 = AsyncMock(spec=httpx.Response)
    mock_resp_400.status_code = 400
    with patch("httpx.AsyncClient.head", AsyncMock(return_value=mock_resp_400)):
        alive, latency, status = await probe_http_tracker("http://tracker.example.com/announce")
        assert alive is True
        assert status == "HTTP 400"

    # Connection error means dead
    with patch("httpx.AsyncClient.head", AsyncMock(side_effect=httpx.ConnectError("Down"))):
        alive, latency, status = await probe_http_tracker("http://tracker.example.com/announce")
        assert alive is False
        assert "ConnectError" in status

def test_tracker_service_sorting_and_fallback():
    svc = TrackerService(urls=[])
    svc.cached_trackers = ["udp://fast.org:1337", "udp://slow.org:1337", "udp://dead.org:1337"]
    
    # Mark fast and slow as alive with latencies, dead as offline
    from torrent_sentinel.models import TrackerHealth
    svc.tracker_health["udp://fast.org:1337"] = TrackerHealth(url="udp://fast.org:1337", is_alive=True, latency_ms=15.0)
    svc.tracker_health["udp://slow.org:1337"] = TrackerHealth(url="udp://slow.org:1337", is_alive=True, latency_ms=120.0)
    svc.tracker_health["udp://dead.org:1337"] = TrackerHealth(url="udp://dead.org:1337", is_alive=False, latency_ms=None)

    healthy_sorted = svc.get_trackers(only_healthy=True)
    assert healthy_sorted == ["udp://fast.org:1337", "udp://slow.org:1337"]

    # If all are dead, fallback returns all cached
    svc.tracker_health["udp://fast.org:1337"].is_alive = False
    svc.tracker_health["udp://slow.org:1337"].is_alive = False
    fallback = svc.get_trackers(only_healthy=True)
    assert len(fallback) == 3

def test_tracker_service_should_refresh():
    from datetime import datetime, timezone, timedelta
    svc = TrackerService(urls=[])
    assert svc.should_refresh(12) is True  # never refreshed yet

    svc.last_refreshed = datetime.now(timezone.utc) - timedelta(hours=5)
    assert svc.should_refresh(12) is False

    svc.last_refreshed = datetime.now(timezone.utc) - timedelta(hours=13)
    assert svc.should_refresh(12) is True

def test_tracker_service_record_transmission_stats():
    svc = TrackerService(urls=[])
    stats = [
        {
            "announce": "udp://tracker.opentrackr.org:1337/announce",
            "lastAnnounceSucceeded": True,
            "lastAnnounceResult": "Success",
            "lastAnnouncePeerCount": 42
        }
    ]
    svc.record_transmission_stats(stats)
    h = svc.tracker_health["udp://tracker.opentrackr.org:1337/announce"]
    assert h.peers_seen == 42
    assert h.is_alive is True
    assert h.status == "Success"
