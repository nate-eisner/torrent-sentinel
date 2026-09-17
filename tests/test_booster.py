import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from torrent_sentinel.models import TorrentInfo, BoostState, ServarrType, ServarrQueueItem
from torrent_sentinel.engine.booster import TorrentBooster, StalledRecord
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.services.tracker_service import TrackerService

@pytest_asyncio.fixture
async def mock_storage(tmp_path):
    db_file = tmp_path / "test_booster.db"
    storage = Storage(str(db_file))
    await storage.initialize()
    return storage

@pytest_asyncio.fixture
async def booster(mock_storage):
    mock_trans = AsyncMock(spec=TransmissionClient)
    mock_trans.add_trackers.return_value = True
    mock_trans.reannounce_torrents.return_value = True
    mock_trans.add_labels.return_value = True
    mock_trans.recheck.return_value = True
    mock_trans.resume.return_value = True
    mock_trans.delete_torrent.return_value = True

    mock_trackers = MagicMock(spec=TrackerService)
    mock_trackers.get_trackers.return_value = ["udp://test.tracker.org:1337/announce"]

    booster_inst = TorrentBooster(
        transmission=mock_trans,
        tracker_service=mock_trackers,
        storage=mock_storage
    )
    return booster_inst

@pytest.mark.asyncio
async def test_booster_stage_1_injection(booster):
    stalled_torrent = TorrentInfo(
        id="10",
        hashString="abcdef123456",
        name="Stalled ISO",
        status="downloading",
        percentDone=0.1,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0
    )

    # 1. First cycle: detects stalled and tracks it
    await booster.run_cycle([stalled_torrent])
    assert "abcdef123456" in booster.stalled_records
    rec = booster.stalled_records["abcdef123456"]
    assert rec.state == BoostState.STALLED

    # 2. Fast-forward stalled duration past STALL_THRESHOLD_MINUTES (e.g. 10m)
    rec.first_stalled_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    # 3. Next cycle: triggers Stage 1 boost
    await booster.run_cycle([stalled_torrent])
    assert rec.state == BoostState.BOOSTING
    assert rec.boost_count == 1
    assert rec.grace_period_expires_at is not None
    booster.transmission.add_trackers.assert_called_once_with("10", ["udp://test.tracker.org:1337/announce"])
    booster.transmission.reannounce_torrents.assert_called_once_with(["10"])

    # Check history event recorded
    events = await booster.storage.get_boost_events()
    assert len(events) >= 1
    assert events[0].action == "trackers_injected"

@pytest.mark.asyncio
async def test_booster_swarm_revived(booster):
    # Set up a torrent that is currently in BOOSTING state
    rec = StalledRecord(torrent_id="10", torrent_hash="abcdef123456")
    rec.state = BoostState.BOOSTING
    booster.stalled_records["abcdef123456"] = rec

    # Torrent recovers healthy download speed and peers
    revived_torrent = TorrentInfo(
        id="10",
        hashString="abcdef123456",
        name="Stalled ISO",
        status="downloading",
        percentDone=0.2,
        rateDownload=500.0 * 1024.0, # 500 KB/s
        rateUpload=50.0 * 1024.0,
        peersConnected=15,
        peersSendingToUs=8
    )

    await booster.run_cycle([revived_torrent])

    # Should be removed from stalled records
    assert "abcdef123456" not in booster.stalled_records

    # History should contain swarm_revived
    events = await booster.storage.get_boost_events()
    revived_events = [e for e in events if e.action == "swarm_revived"]
    assert len(revived_events) == 1
    assert "revived after boosting" in revived_events[0].details

@pytest.mark.asyncio
async def test_booster_auto_repair_error(booster):
    errored_torrent = TorrentInfo(
        id="20",
        hashString="err123",
        name="Error Torrent",
        status="error",
        percentDone=0.5,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0,
        error=3,
        errorString="Data corrupt"
    )

    await booster.run_cycle([errored_torrent])

    # Auto-repair should trigger recheck & resume
    booster.transmission.recheck.assert_called_once_with("20")
    booster.transmission.resume.assert_called_once_with("20")
    assert booster.stalled_records["err123"].recheck_attempted is True

    # Re-running cycle should not trigger recheck again
    await booster.run_cycle([errored_torrent])
    assert booster.transmission.recheck.call_count == 1

@pytest.mark.asyncio
async def test_booster_probation_expiry_without_auto_failover(booster):
    rec = StalledRecord(torrent_id="30", torrent_hash="dead123")
    rec.state = BoostState.BOOSTING
    # Expire grace period
    rec.grace_period_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    booster.stalled_records["dead123"] = rec

    dead_torrent = TorrentInfo(
        id="30",
        hashString="dead123",
        name="Dead Torrent",
        status="stalledDL",
        percentDone=0.0,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0
    )

    # auto-failover is False by default
    await booster.set_auto_failover_enabled(False)
    await booster.run_cycle([dead_torrent])

    assert rec.state == BoostState.PROBATION_EXPIRED
    assert "expired" in rec.status_message.lower()

@pytest.mark.asyncio
async def test_booster_probation_expiry_with_auto_failover(booster):
    rec = StalledRecord(torrent_id="30", torrent_hash="dead123")
    rec.state = BoostState.BOOSTING
    rec.grace_period_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    booster.stalled_records["dead123"] = rec

    dead_torrent = TorrentInfo(
        id="30",
        hashString="dead123",
        name="Dead Torrent",
        status="stalledDL",
        percentDone=0.0,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0
    )

    # Enable auto-failover
    await booster.set_auto_failover_enabled(True)
    await booster.run_cycle([dead_torrent])

    assert rec.state == BoostState.FAILED_OVER
    booster.transmission.delete_torrent.assert_called_once_with("30", delete_files=True)

@pytest.mark.asyncio
async def test_booster_manual_actions(booster):
    torrent = TorrentInfo(
        id="40",
        hashString="manual123",
        name="Manual Test",
        status="downloading",
        percentDone=0.4,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0
    )
    booster.transmission.get_torrents.return_value = [torrent]

    # Manual boost
    ok = await booster.manual_boost("40")
    assert ok is True
    booster.transmission.add_trackers.assert_called_once_with("40", ["udp://test.tracker.org:1337/announce"])

    # Manual recheck
    ok = await booster.manual_recheck("40")
    assert ok is True
    booster.transmission.recheck.assert_called_once_with("40")
    booster.transmission.resume.assert_called_once_with("40")

    # Manual reannounce
    ok = await booster.manual_reannounce("40")
    assert ok is True
    booster.transmission.reannounce_torrents.assert_called_with(["40"])
