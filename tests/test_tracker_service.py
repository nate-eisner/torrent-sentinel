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
