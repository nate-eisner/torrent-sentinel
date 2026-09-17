import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import os
import subprocess

from torrent_sentinel.config import settings
from torrent_sentinel.models import LocationProfile
from torrent_sentinel.vpn import get_vpn_adapter
from torrent_sentinel.vpn.unraid_wireguard import UnraidWireGuardAdapter, WireGuardGatewayAdapter
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage

def test_vpn_adapter_factory():
    with patch.object(settings, "VPN_TYPE", "wireguard"):
        adapter = get_vpn_adapter()
        assert isinstance(adapter, UnraidWireGuardAdapter)

    with patch.object(settings, "VPN_TYPE", "unraid_wireguard"):
        adapter = get_vpn_adapter()
        assert isinstance(adapter, UnraidWireGuardAdapter)

    with patch.object(settings, "VPN_TYPE", "wireguard_gateway"):
        adapter = get_vpn_adapter()
        assert isinstance(adapter, WireGuardGatewayAdapter)

    with patch.object(settings, "VPN_TYPE", "mock"):
        from torrent_sentinel.vpn.mock import MockVPNAdapter
        adapter = get_vpn_adapter()
        assert isinstance(adapter, MockVPNAdapter)

def test_lan_routing_applies_ip_rules():
    adapter = UnraidWireGuardAdapter()
    adapter._default_gw = "172.17.0.1"
    adapter._default_dev = "eth0"
    with patch.object(settings, "LAN_NETWORK", "192.168.1.0/24,10.0.0.0/8"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter._apply_lan_routing()
            
            calls = [call.args[0] for call in mock_run.call_args_list]
            # Verify ip rule was added for both subnets
            assert ["ip", "-4", "rule", "add", "to", "192.168.1.0/24", "table", "main", "pref", "100"] in calls
            assert ["ip", "-4", "rule", "add", "to", "10.0.0.0/8", "table", "main", "pref", "100"] in calls
            # Verify route was replaced via eth0 gateway
            assert ["ip", "-4", "route", "replace", "192.168.1.0/24", "via", "172.17.0.1", "dev", "eth0"] in calls
            assert ["ip", "-4", "route", "replace", "10.0.0.0/8", "via", "172.17.0.1", "dev", "eth0"] in calls

def test_detect_default_gateway_proc_net_route(tmp_path):
    proc_route = tmp_path / "route"
    # Format: Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
    proc_route.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    )
    from unittest.mock import mock_open
    adapter = UnraidWireGuardAdapter()
    adapter._default_gw = None
    adapter._default_dev = None
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=proc_route.read_text())):
            gw_ip, dev = adapter._detect_default_gateway()
            assert gw_ip == "172.17.0.1"
            assert dev == "eth0"

def test_lan_routing_empty():
    adapter = UnraidWireGuardAdapter()
    with patch.object(settings, "LAN_NETWORK", None):
        with patch("subprocess.run") as mock_run:
            adapter._apply_lan_routing()
            assert mock_run.call_count == 0

def test_teardown_existing_interfaces():
    adapter = UnraidWireGuardAdapter()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="us-nyc wg1\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=0)
        ]
        adapter._teardown_existing_interfaces()
        calls = [call.args[0] for call in mock_run.call_args_list]
        assert ["wg", "show", "interfaces"] in calls
        assert ["wg-quick", "down", "us-nyc"] in calls
        assert ["wg-quick", "down", "wg1"] in calls

@pytest.mark.asyncio
async def test_get_current_profile_detection(tmp_path):
    adapter = UnraidWireGuardAdapter()
    adapter.configs_dir = str(tmp_path)
    (tmp_path / "nl-ams.conf").write_text("[Interface]\n")
    (tmp_path / "us-nyc.conf").write_text("[Interface]\n")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="us-nyc\n")
        profile = await adapter.get_current_profile()
        assert profile is not None
        assert profile.id == "us-nyc"
        assert profile.name == "Us Nyc"

@pytest.mark.asyncio
async def test_daemon_startup_initializes_vpn(tmp_path):
    adapter = AsyncMock(spec=UnraidWireGuardAdapter)
    mock_profile = LocationProfile(
        id="us-nyc",
        name="US NYC",
        country="US",
        endpoint="",
        config_file="/app/vpn_configs/us-nyc.conf"
    )
    adapter.get_available_locations.return_value = [mock_profile]
    adapter.get_current_profile.return_value = None
    adapter.rotate_to.return_value = True

    daemon = SentinelDaemon(adapter)
    db_file = tmp_path / "test_sentinel_startup.db"
    daemon.storage = Storage(str(db_file))

    async def stop_after_cycle(*args, **kwargs):
        daemon.stop()

    with patch.object(daemon.ollama, "health_check", AsyncMock(return_value=True)):
        with patch.object(daemon.transmission, "get_torrents", AsyncMock(return_value=[])):
            with patch.object(daemon.decision_engine, "decide_rotation", AsyncMock(return_value=None)):
                with patch("asyncio.sleep", AsyncMock(side_effect=stop_after_cycle)):
                    await daemon.run()

    adapter.rotate_to.assert_called_once_with(mock_profile)

@pytest.mark.asyncio
async def test_daemon_handles_transmission_connect_error(tmp_path, caplog):
    import httpx
    adapter = AsyncMock(spec=UnraidWireGuardAdapter)
    adapter.get_available_locations.return_value = []
    adapter.get_current_profile.return_value = None

    daemon = SentinelDaemon(adapter)
    db_file = tmp_path / "test_sentinel_conn_err.db"
    daemon.storage = Storage(str(db_file))

    async def stop_after_cycle(*args, **kwargs):
        daemon.stop()

    with patch.object(daemon.ollama, "health_check", AsyncMock(return_value=True)):
        with patch.object(daemon.transmission, "get_torrents", AsyncMock(side_effect=httpx.ConnectError("Connection refused"))):
            with patch("asyncio.sleep", AsyncMock(side_effect=stop_after_cycle)):
                await daemon.run()

    # Daemon handled the error gracefully without bubbling up an exception
    assert "Transmission unreachable" in caplog.text
