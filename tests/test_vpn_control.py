import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from datetime import datetime, timezone

from torrent_sentinel.config import settings
from torrent_sentinel.models import LocationProfile, TorrentInfo, OffVpnState, UnifiedTorrentItem, BoostState
from torrent_sentinel.vpn.mock import MockVPNAdapter
from torrent_sentinel.vpn.unraid_wireguard import UnraidWireGuardAdapter
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.api.router import app
from torrent_sentinel.cli import app as cli_app

runner = CliRunner()


def make_profile(pid: str = "us-nyc", name: str = "US New York") -> LocationProfile:
    return LocationProfile(
        id=pid,
        name=name,
        country="US",
        endpoint="1.2.3.4:51820",
        config_file=f"/fake/{pid}.conf"
    )


def make_torrent(tid: str, thash: str, name: str, progress: float = 0.5) -> TorrentInfo:
    return TorrentInfo(
        id=tid,
        hash=thash,
        name=name,
        status="downloading",
        progress=progress,
        rate_download=1024.0,
        rate_upload=0.0,
        peers_connected=5,
        peers_sending_to_us=2,
        num_seeds=2,
        num_leechs=3
    )


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. VPN Adapter Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_vpn_disconnect_and_is_connected():
    adapter = MockVPNAdapter()
    profile = make_profile("us-nyc", "US New York")
    
    assert await adapter.is_connected() is False
    assert await adapter.rotate_to(profile) is True
    assert await adapter.is_connected() is True
    assert (await adapter.get_current_profile()).id == "us-nyc"

    assert await adapter.disconnect() is True
    assert await adapter.is_connected() is False
    assert await adapter.get_current_profile() is None


@pytest.mark.asyncio
async def test_unraid_wireguard_disconnect_and_is_connected():
    adapter = UnraidWireGuardAdapter()
    profile = make_profile("ch-zurich", "CH Zurich")
    adapter._current_profile = profile

    with patch("subprocess.run") as mock_run:
        # 1. is_connected returns True when returncode is 0 and stdout has output
        mock_run.return_value = MagicMock(returncode=0, stdout="ch-zurich\n")
        assert await adapter.is_connected() is True

        # 2. is_connected returns False when returncode is non-zero
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert await adapter.is_connected() is False

        # 3. disconnect tears down interface, restores DNS, applies LAN routing, clears profile
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with patch.object(adapter, "_ensure_resolv_conf") as mock_dns, \
             patch.object(adapter, "_apply_lan_routing") as mock_lan:
            success = await adapter.disconnect()
            assert success is True
            assert adapter._current_profile is None
            mock_dns.assert_called_once()
            mock_lan.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Storage Persistence Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_storage_vpn_enabled_and_off_vpn_state(tmp_path):
    db_file = str(tmp_path / "test_sentinel.db")
    storage = Storage(db_path=db_file)
    await storage.initialize()

    # Initial defaults
    assert await storage.get_vpn_enabled() is True
    assert await storage.get_off_vpn_state() is None

    # Update VPN enabled
    await storage.set_vpn_enabled(False)
    assert await storage.get_vpn_enabled() is False

    await storage.set_vpn_enabled(True)
    assert await storage.get_vpn_enabled() is True

    # Save Off-VPN State
    state = OffVpnState(
        is_active=True,
        torrent_id="42",
        torrent_hash="aabbcc112233",
        torrent_name="Ubuntu Linux ISO",
        paused_torrent_ids=["10", "11"],
        previous_vpn_location="nl-ams",
        started_at=datetime.now(timezone.utc),
        auto_restore_on_complete=True
    )
    await storage.set_off_vpn_state(state)

    loaded = await storage.get_off_vpn_state()
    assert loaded is not None
    assert loaded.is_active is True
    assert loaded.torrent_id == "42"
    assert loaded.torrent_name == "Ubuntu Linux ISO"
    assert loaded.paused_torrent_ids == ["10", "11"]
    assert loaded.previous_vpn_location == "nl-ams"
    assert loaded.auto_restore_on_complete is True

    # Clear Off-VPN State
    await storage.set_off_vpn_state(None)
    assert await storage.get_off_vpn_state() is None


# ---------------------------------------------------------------------------
# 3. Daemon VPN & Solo Off-VPN Lifecycle Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daemon_disable_and_enable_vpn(tmp_path):
    db_file = str(tmp_path / "sentinel_daemon.db")
    storage = Storage(db_path=db_file)
    await storage.initialize()

    adapter = MockVPNAdapter()
    daemon = SentinelDaemon(adapter)
    daemon.storage = storage
    daemon.decision_engine.notifications = AsyncMock()

    profile = make_profile("se-sto", "SE Stockholm")
    await daemon.vpn_adapter.rotate_to(profile)

    # Disable VPN
    res = await daemon.disable_vpn()
    assert res["status"] == "success"
    assert res["vpn_enabled"] is False
    assert await daemon.is_vpn_enabled() is False
    assert await daemon.vpn_adapter.is_connected() is False
    daemon.decision_engine.notifications.dispatch_alert.assert_called()

    # Enable VPN
    with patch.object(daemon.vpn_adapter, "get_available_locations", AsyncMock(return_value=[profile])):
        res = await daemon.enable_vpn()
        assert res["status"] == "success"
        assert res["vpn_enabled"] is True
        assert await daemon.is_vpn_enabled() is True
        assert await daemon.vpn_adapter.is_connected() is True


@pytest.mark.asyncio
async def test_daemon_start_and_restore_solo_off_vpn(tmp_path):
    db_file = str(tmp_path / "sentinel_daemon_off.db")
    storage = Storage(db_path=db_file)
    await storage.initialize()

    adapter = MockVPNAdapter()
    daemon = SentinelDaemon(adapter)
    daemon.storage = storage
    daemon.decision_engine.notifications = AsyncMock()
    daemon.transmission = AsyncMock()
    daemon.booster = AsyncMock()

    profile = make_profile("ca-tor", "CA Toronto")
    await daemon.vpn_adapter.rotate_to(profile)

    t1 = TorrentInfo(
        id="1", hash="1111", name="Target Torrent", status="downloading",
        progress=0.1, rate_download=100.0, rate_upload=10.0,
        peers_connected=5, peers_sending_to_us=2, num_seeds=1, num_leechs=4
    )
    t2 = TorrentInfo(
        id="2", hash="2222", name="Other Torrent", status="downloading",
        progress=0.5, rate_download=500.0, rate_upload=50.0,
        peers_connected=10, peers_sending_to_us=5, num_seeds=3, num_leechs=2
    )
    t3 = TorrentInfo(
        id="3", hash="3333", name="Already Stopped Torrent", status="0",
        progress=0.8, rate_download=0.0, rate_upload=0.0,
        peers_connected=0, peers_sending_to_us=0, num_seeds=0, num_leechs=0
    )

    daemon.transmission.get_torrents.return_value = [t1, t2, t3]

    # Start Solo Off-VPN mode for t1
    res = await daemon.start_solo_off_vpn("1", auto_restore=True)
    assert res["status"] == "success"
    assert res["torrent_id"] == "1"

    state = await daemon.get_off_vpn_state()
    assert state is not None
    assert state.is_active is True
    assert state.torrent_id == "1"
    # Only active torrent (t2) should be in paused_torrent_ids, t3 was already stopped
    assert state.paused_torrent_ids == ["2"]
    assert state.previous_vpn_location == "ca-tor"

    # Transmission checks
    daemon.transmission.pause_torrents.assert_called_with(["2", "3"])
    daemon.transmission.resume.assert_called_with("1")
    assert await daemon.vpn_adapter.is_connected() is False

    # Restore from Solo Off-VPN mode
    with patch.object(daemon.vpn_adapter, "get_available_locations", AsyncMock(return_value=[profile])):
        restored = await daemon.restore_from_solo_off_vpn()
        assert restored["status"] == "success"
        assert await daemon.is_vpn_enabled() is True
        assert await daemon.vpn_adapter.is_connected() is True
        daemon.transmission.resume_torrents.assert_called_with(["2"])
        assert await daemon.get_off_vpn_state() is None


@pytest.mark.asyncio
async def test_daemon_loop_auto_restore_on_download_complete(tmp_path):
    db_file = str(tmp_path / "sentinel_auto_restore.db")
    storage = Storage(db_path=db_file)
    await storage.initialize()

    adapter = MockVPNAdapter()
    daemon = SentinelDaemon(adapter)
    daemon.storage = storage
    daemon.transmission = AsyncMock()
    daemon.restore_from_solo_off_vpn = AsyncMock()

    # Setup active off-vpn state
    state = OffVpnState(
        is_active=True,
        torrent_id="99",
        torrent_hash="9999",
        torrent_name="Big Download",
        paused_torrent_ids=["100"],
        auto_restore_on_complete=True
    )
    await storage.set_off_vpn_state(state)

    # Torrent reaches 100% (progress = 1.0)
    finished_torrent = TorrentInfo(
        id="99", hash="9999", name="Big Download", status="seeding",
        progress=1.0, rate_download=0.0, rate_upload=10.0,
        peers_connected=5, peers_sending_to_us=0, num_seeds=0, num_leechs=5
    )
    daemon.transmission.get_torrents.return_value = [finished_torrent]

    await daemon.check_off_vpn_lifecycle([finished_torrent])

    # Should pause finished torrent so it doesn't seed off-vpn, then trigger auto restore
    daemon.transmission.pause.assert_called_with("99")
    daemon.restore_from_solo_off_vpn.assert_called_once()


# ---------------------------------------------------------------------------
# 4. API Endpoints Tests
# ---------------------------------------------------------------------------

def test_api_vpn_status_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.is_vpn_enabled = AsyncMock(return_value=True)
        mock_daemon.vpn_adapter.is_connected = AsyncMock(return_value=True)
        mock_daemon.vpn_adapter.get_current_profile = AsyncMock(return_value=make_profile("us-mia", "US Miami"))
        mock_daemon.get_off_vpn_state = AsyncMock(return_value=None)

        resp = client.get("/api/vpn/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vpn_enabled"] is True
        assert data["connected"] is True
        assert data["current_location"] == "US Miami"
        assert data["off_vpn_mode"] is False


def test_api_vpn_disable_and_enable_endpoints(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.disable_vpn = AsyncMock(return_value={"status": "success", "vpn_enabled": False, "connected": False})
        mock_daemon.enable_vpn = AsyncMock(return_value={"status": "success", "vpn_enabled": True, "connected": True})

        resp_dis = client.post("/api/vpn/disable")
        assert resp_dis.status_code == 200
        assert resp_dis.json()["vpn_enabled"] is False

        resp_en = client.post("/api/vpn/enable")
        assert resp_en.status_code == 200
        assert resp_en.json()["vpn_enabled"] is True


def test_api_vpn_toggle_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.is_vpn_enabled = AsyncMock(return_value=True)
        mock_daemon.disable_vpn = AsyncMock(return_value={"status": "success", "vpn_enabled": False, "connected": False})

        resp = client.post("/api/vpn/toggle")
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is False
        mock_daemon.disable_vpn.assert_called_once()


def test_api_torrents_run_off_vpn_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.start_solo_off_vpn = AsyncMock(return_value={
            "status": "success",
            "torrent_id": "5",
            "paused_torrent_ids": ["1", "2"]
        })

        resp = client.post("/api/torrents/5/run-off-vpn", json={"auto_restore_on_complete": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["torrent_id"] == "5"
        assert data["paused_torrent_ids"] == ["1", "2"]


def test_api_torrents_off_vpn_restore_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.restore_from_solo_off_vpn = AsyncMock(return_value={
            "status": "success",
            "resumed_torrent_ids": ["1", "2"]
        })

        resp = client.post("/api/torrents/off-vpn/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["resumed_torrent_ids"] == ["1", "2"]


def test_api_status_includes_vpn_and_off_vpn_fields(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.running = True
        mock_daemon.is_vpn_enabled = AsyncMock(return_value=False)
        mock_daemon.vpn_adapter.is_connected = AsyncMock(return_value=False)
        mock_daemon.vpn_adapter.get_current_profile = AsyncMock(return_value=None)
        mock_daemon.booster.is_auto_failover_enabled = AsyncMock(return_value=False)
        mock_daemon.tracker_service.get_trackers = MagicMock(return_value=[])

        off_state = OffVpnState(
            is_active=True,
            torrent_id="77",
            torrent_name="My Exclusive File",
            paused_torrent_ids=["1", "2", "3"]
        )
        mock_daemon.get_off_vpn_state = AsyncMock(return_value=off_state)

        with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[])):
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["vpn_enabled"] is False
            assert data["vpn_connected"] is False
            assert data["off_vpn_mode"] is True
            assert data["off_vpn_torrent_id"] == "77"
            assert data["off_vpn_torrent_name"] == "My Exclusive File"
            assert data["off_vpn_paused_count"] == 3


# ---------------------------------------------------------------------------
# 5. CLI Command Tests
# ---------------------------------------------------------------------------

def test_cli_vpn_status():
    mock_adapter = AsyncMock()
    mock_adapter.is_connected = AsyncMock(return_value=True)
    mock_adapter.get_current_profile = AsyncMock(return_value=make_profile("uk-lon", "UK London"))

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.engine.storage.Storage.get_vpn_enabled", AsyncMock(return_value=True)), \
         patch("torrent_sentinel.engine.storage.Storage.get_off_vpn_state", AsyncMock(return_value=None)):
        
        result = runner.invoke(cli_app, ["vpn", "status"])
        assert result.exit_code == 0
        assert "VPN Gateway Status" in result.output
        assert "CONNECTED" in result.output
        assert "UK London" in result.output


def test_cli_vpn_disable():
    mock_adapter = AsyncMock()
    mock_adapter.disconnect = AsyncMock(return_value=True)

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.engine.storage.Storage.set_vpn_enabled", AsyncMock()) as mock_set:
        
        result = runner.invoke(cli_app, ["vpn", "disable"])
        assert result.exit_code == 0
        assert "VPN Gateway disabled" in result.output
        mock_set.assert_called_with(False)
        mock_adapter.disconnect.assert_called_once()


def test_cli_vpn_enable():
    mock_profile = make_profile("de-fra", "DE Frankfurt")
    mock_adapter = AsyncMock()
    mock_adapter.get_available_locations = AsyncMock(return_value=[mock_profile])
    mock_adapter.rotate_to = AsyncMock(return_value=True)

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.engine.storage.Storage.set_vpn_enabled", AsyncMock()) as mock_set:
        
        result = runner.invoke(cli_app, ["vpn", "enable", "de-fra"])
        assert result.exit_code == 0
        assert "VPN Gateway enabled and connected to:" in result.output
        assert "DE Frankfurt" in result.output
        mock_set.assert_called_with(True)
        mock_adapter.rotate_to.assert_called_once()


def test_cli_off_vpn_status_inactive():
    with patch("torrent_sentinel.engine.storage.Storage.get_off_vpn_state", AsyncMock(return_value=None)):
        result = runner.invoke(cli_app, ["off-vpn", "status"])
        assert result.exit_code == 0
        assert "Solo Off-VPN mode is inactive" in result.output


def test_cli_off_vpn_status_active():
    state = OffVpnState(
        is_active=True,
        torrent_id="12",
        torrent_name="Linux Distro",
        paused_torrent_ids=["1", "2"]
    )
    with patch("torrent_sentinel.engine.storage.Storage.get_off_vpn_state", AsyncMock(return_value=state)):
        result = runner.invoke(cli_app, ["off-vpn", "status"])
        assert result.exit_code == 0
        assert "SOLO OFF-VPN MODE IS ACTIVE" in result.output
        assert "Linux Distro" in result.output
        assert "Paused Queue Torrents:" in result.output


def test_cli_off_vpn_start_and_stop():
    t1 = TorrentInfo(id="10", hash="abc", name="Special File", status="downloading", progress=0.1, rate_download=10.0, rate_upload=0.0, peers_connected=2, peers_sending_to_us=1, num_seeds=1, num_leechs=1)
    t2 = TorrentInfo(id="20", hash="def", name="Other File", status="downloading", progress=0.2, rate_download=10.0, rate_upload=0.0, peers_connected=2, peers_sending_to_us=1, num_seeds=1, num_leechs=1)

    mock_adapter = AsyncMock()
    mock_adapter.get_current_profile = AsyncMock(return_value=None)
    mock_adapter.disconnect = AsyncMock(return_value=True)

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[t1, t2])), \
         patch("torrent_sentinel.clients.transmission.TransmissionClient.pause_torrents", AsyncMock()) as mock_pause, \
         patch("torrent_sentinel.clients.transmission.TransmissionClient.resume", AsyncMock()) as mock_resume, \
         patch("torrent_sentinel.engine.storage.Storage.set_off_vpn_state", AsyncMock()) as mock_set_state:
        
        result = runner.invoke(cli_app, ["off-vpn", "start", "10"])
        assert result.exit_code == 0
        assert "Solo Off-VPN Mode Active" in result.output
        assert "Special File" in result.output
        mock_pause.assert_called_with(["20"])
        mock_resume.assert_called_with("10")
        mock_adapter.disconnect.assert_called_once()
        mock_set_state.assert_called_once()

    # Test stop command
    active_state = OffVpnState(
        is_active=True,
        torrent_id="10",
        torrent_name="Special File",
        paused_torrent_ids=["20"]
    )
    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.engine.storage.Storage.get_off_vpn_state", AsyncMock(return_value=active_state)), \
         patch("torrent_sentinel.engine.storage.Storage.set_vpn_enabled", AsyncMock()) as mock_set_vpn, \
         patch("torrent_sentinel.clients.transmission.TransmissionClient.resume_torrents", AsyncMock()) as mock_resume_fleet, \
         patch("torrent_sentinel.engine.storage.Storage.set_off_vpn_state", AsyncMock()) as mock_clear_state:

        mock_adapter.get_available_locations = AsyncMock(return_value=[])

        result = runner.invoke(cli_app, ["off-vpn", "stop"])
        assert result.exit_code == 0
        assert "Solo Off-VPN mode ended" in result.output
        mock_set_vpn.assert_called_with(True)
        mock_resume_fleet.assert_called_with(["20"])
        mock_clear_state.assert_called_with(None)


# ---------------------------------------------------------------------------
# 6. Port Test & Tracker Diagnostics Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transmission_port_test():
    client = TransmissionClient()
    with patch.object(client, "_post_rpc", AsyncMock(side_effect=[
        {"result": "success", "arguments": {"peer-port": 51413}},
        {"result": "success", "arguments": {"port-is-open": True}}
    ])):
        res = await client.port_test()
        assert res["status"] == "success"
        assert res["port_open"] is True
        assert res["port"] == 51413


def test_tracker_classification_and_summarization():
    from torrent_sentinel.engine.diagnostics import classify_tracker_stat, summarize_torrent_trackers

    # 1. Healthy tracker
    stat_healthy = {
        "host": "tracker.opentrackr.org",
        "announce": "udp://tracker.opentrackr.org:1337/announce",
        "lastAnnounceResult": "Success",
        "lastAnnounceSucceeded": True,
        "seederCount": 50,
        "leecherCount": 10
    }
    c_healthy = classify_tracker_stat(stat_healthy)
    assert c_healthy["status"] == "healthy"
    assert c_healthy["is_vpn_block_suspect"] is False

    # 2. Blocked / 403 tracker
    stat_blocked = {
        "host": "tracker.private.org",
        "announce": "https://tracker.private.org/announce",
        "lastAnnounceResult": "HTTP 403 Forbidden - Cloudflare Turnstile verification required",
        "lastAnnounceSucceeded": False,
        "seederCount": 0,
        "leecherCount": 0
    }
    c_blocked = classify_tracker_stat(stat_blocked)
    assert c_blocked["status"] == "blocked"
    assert c_blocked["is_vpn_block_suspect"] is True

    # 3. Timed out tracker
    stat_timeout = {
        "host": "tracker.dead.org",
        "announce": "udp://tracker.dead.org:6969/announce",
        "lastAnnounceResult": "Connection timed out",
        "lastAnnounceSucceeded": False,
        "lastAnnounceTimedOut": True,
        "seederCount": 0,
        "leecherCount": 0
    }
    c_timeout = classify_tracker_stat(stat_timeout)
    assert c_timeout["status"] == "unreachable"

    # Summarize
    summary = summarize_torrent_trackers([stat_healthy, stat_blocked, stat_timeout])
    assert summary["total_trackers"] == 3
    assert summary["healthy_count"] == 1
    assert summary["blocked_count"] == 1
    assert summary["unreachable_count"] == 1
    assert summary["vpn_blocked_suspect"] is True


def test_api_network_port_test_endpoint(client):
    with patch("torrent_sentinel.clients.transmission.TransmissionClient.port_test", AsyncMock(return_value={
        "status": "success",
        "port_open": False,
        "port": 51413,
        "message": "Peer listening port 51413 is CLOSED / FIREWALLED"
    })):
        resp = client.get("/api/network/port-test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["port_open"] is False
        assert data["port"] == 51413
        assert "CLOSED" in data["message"]


def test_api_torrents_trackers_endpoint(client):
    torrent = make_torrent("42", "424242", "Stuck Torrent", progress=0.1)
    torrent.tracker_stats = [
        {
            "host": "tracker.blocked.com",
            "announce": "https://tracker.blocked.com/announce",
            "lastAnnounceResult": "HTTP 403 Forbidden",
            "lastAnnounceSucceeded": False,
            "seederCount": 0,
            "leecherCount": 0,
            "lastAnnouncePeerCount": 0
        }
    ]
    with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[torrent])):
        resp = client.get("/api/torrents/42/trackers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["torrent_id"] == "42"
        assert data["total_trackers"] == 1
        assert data["blocked_count"] == 1
        assert data["vpn_blocked_suspect"] is True
        assert data["trackers"][0]["status"] == "blocked"


def test_cli_test_port_command():
    mock_adapter = AsyncMock()
    mock_adapter.is_connected = AsyncMock(return_value=True)
    mock_adapter.get_current_profile = AsyncMock(return_value=make_profile("loc-1", "Netherlands WireGuard"))

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.clients.transmission.TransmissionClient.port_test", AsyncMock(return_value={
             "status": "success",
             "port_open": True,
             "port": 51413,
             "message": "Port 51413 is OPEN"
         })):
        result = runner.invoke(cli_app, ["test-port"])
        assert result.exit_code == 0
        assert "OPEN" in result.output
        assert "51413" in result.output
        assert "Netherlands WireGuard" in result.output


def test_cli_trackers_command():
    torrent = make_torrent("10", "abc12345", "My Test Torrent", progress=0.2)
    torrent.tracker_stats = [
        {
            "host": "tracker.test.org",
            "announce": "https://tracker.test.org/announce",
            "lastAnnounceResult": "HTTP 403 Forbidden",
            "lastAnnounceSucceeded": False,
            "seederCount": 0,
            "leecherCount": 0,
            "lastAnnouncePeerCount": 0
        }
    ]
    with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[torrent])):
        result = runner.invoke(cli_app, ["trackers", "10"])
        assert result.exit_code == 0
        assert "My Test Torrent" in result.output
        assert "BLOCKED" in result.output
        assert "VPN IP Block Detected" in result.output


def test_api_locations_and_vpn_switch(client):
    p1 = make_profile("us-nyc", "US New York")
    p2 = make_profile("nl-ams", "NL Amsterdam")
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.vpn_adapter.get_available_locations = AsyncMock(return_value=[p1, p2])
        mock_daemon.vpn_adapter.get_current_profile = AsyncMock(return_value=p1)
        mock_daemon.decision_engine.execute_rotation = AsyncMock(return_value=True)

        resp = client.get("/api/locations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["current"] == "us-nyc"
        assert len(data["locations"]) == 2
        assert data["locations"][0]["id"] == "us-nyc"
        assert data["locations"][1]["id"] == "nl-ams"

        # Test POST /api/vpn/switch with preferred location
        resp_switch = client.post("/api/vpn/switch", json={"location": "nl-ams"})
        assert resp_switch.status_code == 200
        assert resp_switch.json()["message"] == "Rotation triggered"


def test_cli_vpn_locations_and_switch():
    p1 = make_profile("us-nyc", "US New York")
    p2 = make_profile("nl-ams", "NL Amsterdam")
    mock_adapter = AsyncMock()
    mock_adapter.get_available_locations = AsyncMock(return_value=[p1, p2])
    mock_adapter.get_current_profile = AsyncMock(return_value=p1)
    mock_adapter.rotate_to = AsyncMock(return_value=True)

    with patch("torrent_sentinel.cli.get_vpn_adapter", return_value=mock_adapter), \
         patch("torrent_sentinel.engine.storage.Storage.get_scoreboard", AsyncMock(return_value=[])):
        res_loc = runner.invoke(cli_app, ["vpn", "locations"])
        assert res_loc.exit_code == 0
        assert "WireGuard VPN Locations" in res_loc.output
        assert "us-nyc" in res_loc.output
        assert "nl-ams" in res_loc.output

        res_switch = runner.invoke(cli_app, ["vpn", "switch", "nl-ams"])
        assert res_switch.exit_code == 0
        assert "Switching VPN gateway to" in res_switch.output
        assert "NL Amsterdam" in res_switch.output


def test_ui_vpn_picker_elements():
    import os
    index_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "torrent_sentinel", "api", "static", "index.html"))
    assert os.path.exists(index_path)
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert "vpn-location-select" in html
    assert "modal-vpn-picker" in html
    assert "openVpnPickerModal" in html
    assert "executeVpnLocationAction" in html
    assert "filterVpnPickerLocations" in html
