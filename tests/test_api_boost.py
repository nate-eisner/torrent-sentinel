import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from torrent_sentinel.api.router import app
from torrent_sentinel.models import TorrentInfo, BoostState, UnifiedTorrentItem

@pytest.fixture
def client():
    return TestClient(app)

def test_api_status_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.running = True
        mock_daemon.vpn_adapter.get_current_profile = AsyncMock(return_value=None)
        mock_daemon.booster.is_auto_failover_enabled = AsyncMock(return_value=False)
        mock_daemon.tracker_service.get_trackers = MagicMock(return_value=["tracker1", "tracker2"])

        with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[])):
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["daemon_running"] is True
            assert data["boost_enabled"] is True
            assert data["auto_failover_enabled"] is False
            assert data["cached_trackers_count"] == 2

def test_api_torrents_unified_endpoint(client):
    mock_item = UnifiedTorrentItem(
        id="1",
        hash="abc12345",
        name="Test Item",
        status="downloading",
        progress=0.45,
        rate_download=102400.0,
        rate_upload=51200.0,
        peers_connected=10,
        peers_sending_to_us=4,
        num_seeds=4,
        num_leechs=6,
        boost_state=BoostState.BOOSTING,
        status_message="In probation"
    )
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.get_unified_queue = AsyncMock(return_value=[mock_item])
        resp = client.get("/api/torrents")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["name"] == "Test Item"
        assert items[0]["boost_state"] == "boosting"
        assert items[0]["num_seeds"] == 4

def test_api_boost_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.manual_boost = AsyncMock(return_value=True)
        resp = client.post("/api/torrents/abc12345/boost")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

def test_api_recheck_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.manual_recheck = AsyncMock(return_value=True)
        resp = client.post("/api/torrents/1/recheck")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

def test_api_failover_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.manual_failover = AsyncMock(return_value=True)
        resp = client.post("/api/torrents/1/failover")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

def test_api_toggle_auto_failover_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.set_auto_failover_enabled = AsyncMock(return_value=None)
        resp = client.post("/api/settings/auto-failover", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["auto_failover_enabled"] is True
        mock_daemon.booster.set_auto_failover_enabled.assert_called_once_with(True)

def test_api_trackers_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.tracker_service.get_trackers = MagicMock(return_value=["udp://t1.org:1337", "udp://t2.org:1337"])
        resp = client.get("/api/trackers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["trackers"]) == 2

def test_api_trackers_refresh_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.tracker_service.refresh_trackers = AsyncMock(return_value=["udp://t1.org:1337"])
        resp = client.post("/api/trackers/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
