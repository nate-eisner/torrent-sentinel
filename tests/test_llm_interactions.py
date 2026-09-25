import json
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from torrent_sentinel.models import (
    TorrentInfo,
    BoostState,
    TorrentVerdict,
    RecommendedAction,
    TorrentJudgement,
    SwarmAssessment
)
from torrent_sentinel.clients.ollama import OllamaClient, _clean_json_content
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.diagnostics import Diagnostics
from torrent_sentinel.engine.booster import TorrentBooster, StalledRecord
from torrent_sentinel.api.router import app
from torrent_sentinel.cli import app as cli_app

runner = CliRunner()

@pytest.fixture
def client():
    return TestClient(app)

@pytest_asyncio.fixture
async def mock_storage(tmp_path):
    db_file = tmp_path / "test_llm.db"
    storage = Storage(str(db_file))
    await storage.initialize()
    return storage


# ---------------------------------------------------------------------------
# 1. OllamaClient Tests
# ---------------------------------------------------------------------------

def test_clean_json_content():
    # Markdown backticks
    raw_md = "```json\n{\"verdict\": \"healthy\"}\n```"
    assert _clean_json_content(raw_md) == '{"verdict": "healthy"}'

    # Leading / trailing chat prose
    raw_prose = "Here is your JSON analysis:\n{\"verdict\": \"needs_boost\"}\nHope this helps!"
    assert _clean_json_content(raw_prose) == '{"verdict": "needs_boost"}'

    # Plain clean JSON
    plain = '{"verdict": "dead_swarm"}'
    assert _clean_json_content(plain) == '{"verdict": "dead_swarm"}'


@pytest.mark.asyncio
async def test_ollama_judge_single_torrent_success():
    client = OllamaClient(base_url="http://localhost:11434", model="llama3")
    sample_torrent = {
        "id": "1",
        "hash": "abc123hash",
        "name": "Ubuntu.iso",
        "progress_percent": 50.0,
        "rate_download_kbps": 0.0,
        "peers_connected": 2,
        "peers_sending_to_us": 0
    }

    mock_llm_json = {
        "verdict": "stalled_waiting",
        "viability_score": 0.6,
        "recommended_action": "wait",
        "action_explanation": "Wait for seed connect; peers are present.",
        "confidence": 0.85,
        "reasoning": "Peers are present in swarm; wait for seed connect.",
        "tracker_analysis": "Tracker returned 2 peers."
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"content": f"```json\n{json.dumps(mock_llm_json)}\n```"}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        judgement = await client.judge_single_torrent(sample_torrent, "Is this stalled?")

        assert judgement is not None
        assert judgement.torrent_hash == "abc123hash"
        assert judgement.torrent_id == "1"
        assert judgement.verdict == TorrentVerdict.STALLED_WAITING
        assert judgement.recommended_action == RecommendedAction.WAIT
        assert judgement.confidence == 0.85
        assert judgement.viability_score == 0.6
        assert "Peers are present" in judgement.reasoning


@pytest.mark.asyncio
async def test_ollama_judge_single_torrent_fallback_on_error():
    client = OllamaClient(base_url="http://localhost:11434", model="llama3")
    sample_torrent = {"id": "1", "hash": "badhash", "name": "Broken.iso"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Ollama server down")
        judgement = await client.judge_single_torrent(sample_torrent)
        assert judgement is None


@pytest.mark.asyncio
async def test_ollama_assess_entire_swarm():
    client = OllamaClient(base_url="http://localhost:11434", model="llama3")
    torrents_data = [{"id": "1", "hash": "h1", "name": "T1", "rate_download": 0, "peers_connected": 0}]
    vpn_data = {"current_location": "us-ny", "consecutive_stalls": 3}

    mock_swarm_json = {
        "overall_summary": "Swarm is choking on current VPN server.",
        "vpn_health_verdict": "likely_throttled",
        "should_rotate_vpn": True,
        "vpn_reasoning": "All active torrents stalled simultaneously.",
        "individual_assessments": [
            {
                "torrent_id": "1",
                "torrent_hash": "h1",
                "torrent_name": "T1",
                "verdict": "stalled_waiting",
                "viability_score": 0.5,
                "recommended_action": "wait",
                "action_explanation": "Wait for VPN rotation",
                "confidence": 0.8,
                "reasoning": "Stalled due to VPN"
            }
        ],
        "recommended_actions": [
            {"action": "rotate_vpn", "target": "vpn", "priority": "high", "description": "Rotate VPN server"}
        ]
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"content": json.dumps(mock_swarm_json)}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        assessment = await client.assess_entire_swarm(torrents_data, vpn_data)

        assert assessment is not None
        assert "Swarm is choking" in assessment.overall_summary
        assert assessment.vpn_health_verdict == "likely_throttled"
        assert assessment.should_rotate_vpn is True
        assert len(assessment.torrent_judgements) == 1
        assert assessment.torrent_judgements[0].verdict == TorrentVerdict.STALLED_WAITING


@pytest.mark.asyncio
async def test_ollama_chat_about_downloads():
    client = OllamaClient(base_url="http://localhost:11434", model="llama3")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"content": "Your torrents are downloading smoothly at 5MB/s total."}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        answer = await client.chat_about_downloads(
            message="How are my downloads?",
            context={"torrents": []},
            history=[{"role": "user", "content": "Hello"}]
        )
        assert "downloading smoothly" in answer


# ---------------------------------------------------------------------------
# 2. Storage Tests for Judgements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_storage_judgement_lifecycle(mock_storage):
    j1 = TorrentJudgement(
        torrent_id="1",
        torrent_hash="hash1",
        torrent_name="Linux Mint",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
        verdict=TorrentVerdict.NEEDS_BOOST,
        viability_score=0.75,
        recommended_action=RecommendedAction.BOOST_TRACKERS,
        action_explanation="Inject trackers",
        confidence=0.9,
        reasoning="Few peers, extra trackers should help."
    )
    await mock_storage.record_judgement(j1)

    latest = await mock_storage.get_latest_judgement("hash1")
    assert latest is not None
    assert latest.torrent_hash == "hash1"
    assert latest.verdict == TorrentVerdict.NEEDS_BOOST

    # Record newer judgement
    j2 = TorrentJudgement(
        torrent_id="1",
        torrent_hash="hash1",
        torrent_name="Linux Mint",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.HEALTHY,
        viability_score=0.95,
        recommended_action=RecommendedAction.WAIT,
        action_explanation="Keep downloading",
        confidence=0.95,
        reasoning="Download resumed at high rate."
    )
    await mock_storage.record_judgement(j2)

    latest_updated = await mock_storage.get_latest_judgement("hash1")
    assert latest_updated.verdict == TorrentVerdict.HEALTHY

    all_latest = await mock_storage.get_all_latest_judgements()
    assert "hash1" in all_latest
    assert all_latest["hash1"].verdict == TorrentVerdict.HEALTHY

    history = await mock_storage.get_judgements_for_torrent("hash1", limit=10)
    assert len(history) == 2


# ---------------------------------------------------------------------------
# 3. Diagnostics Engine Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diagnostics_context_and_judge():
    mock_trans = AsyncMock()
    mock_ollama = AsyncMock(spec=OllamaClient)

    diagnostics = Diagnostics(mock_trans, mock_ollama)

    sample_torrent = TorrentInfo(
        id="10",
        hashString="cachehash",
        name="Ubuntu Linux",
        status="downloading",
        percentDone=0.45,
        rateDownload=102400.0,
        rateUpload=51200.0,
        peersConnected=10,
        peersSendingToUs=4,
        peersGettingFromUs=2,
        eta=3600,
        trackerStats=[
            {
                "announce": "udp://tracker.openbittorrent.com:6969",
                "host": "tracker.openbittorrent.com",
                "lastAnnounceResult": "Success",
                "lastAnnounceSucceeded": True,
                "seederCount": 25,
                "leecherCount": 5,
                "lastAnnouncePeerCount": 10
            }
        ]
    )

    context = diagnostics.build_torrent_context(sample_torrent)
    assert context["id"] == "10"
    assert context["hash"] == "cachehash"
    assert context["name"] == "Ubuntu Linux"
    assert context["progress_percent"] == 45.0
    assert context["peers_connected"] == 10
    assert len(context["trackers"]) == 1
    assert context["trackers"][0]["reported_seeders"] == 25

    mock_judgement = TorrentJudgement(
        torrent_id="10",
        torrent_hash="cachehash",
        torrent_name="Ubuntu Linux",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.HEALTHY,
        viability_score=0.9,
        recommended_action=RecommendedAction.WAIT,
        action_explanation="Download is healthy",
        confidence=0.95,
        reasoning="Healthy swarm."
    )
    mock_ollama.judge_single_torrent.return_value = mock_judgement

    res = await diagnostics.judge_torrent(sample_torrent, user_prompt="Check seeders")
    assert res == mock_judgement
    assert mock_ollama.judge_single_torrent.call_count == 1


# ---------------------------------------------------------------------------
# 4. Booster LLM-Assisted Failover Integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_booster_probation_expiry_deferred_by_llm(mock_storage):
    mock_trans = AsyncMock()
    mock_trackers = MagicMock()
    mock_trackers.get_trackers.return_value = ["udp://tracker.org:1337"]
    mock_trackers.get_health_summary.return_value = {"total_discovered": 10, "healthy_count": 8}
    mock_ollama = AsyncMock(spec=OllamaClient)

    diagnostics = Diagnostics(mock_trans, mock_ollama)

    booster = TorrentBooster(
        transmission=mock_trans,
        tracker_service=mock_trackers,
        storage=mock_storage,
        diagnostics=diagnostics
    )

    rec = StalledRecord(torrent_id="99", torrent_hash="stalled_hash")
    rec.state = BoostState.BOOSTING
    rec.grace_period_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    booster.stalled_records["stalled_hash"] = rec

    stalled_torrent = TorrentInfo(
        id="99",
        hashString="stalled_hash",
        name="Stalled But Salvageable",
        status="stalledDL",
        percentDone=0.3,
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=1,
        peersSendingToUs=0
    )

    await booster.set_auto_failover_enabled(True)
    await booster.set_llm_assisted_failover_enabled(True)

    # LLM recommends WAIT
    mock_judgement = TorrentJudgement(
        torrent_id="99",
        torrent_hash="stalled_hash",
        torrent_name="Stalled But Salvageable",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.STALLED_WAITING,
        viability_score=0.7,
        recommended_action=RecommendedAction.WAIT,
        action_explanation="Wait for seeders to reconnect",
        confidence=0.88,
        reasoning="Active seeds are intermittent; recommended waiting longer."
    )
    diagnostics.judge_torrent = AsyncMock(return_value=mock_judgement)

    # Run cycle
    await booster.run_cycle([stalled_torrent])

    # Deferral: probation should NOT be expired, grace period extended, torrent NOT deleted
    assert rec.state == BoostState.BOOSTING
    assert rec.grace_period_expires_at > datetime.now(timezone.utc)
    assert "AI advised wait" in rec.status_message
    mock_trans.delete_torrent.assert_not_called()


@pytest.mark.asyncio
async def test_booster_probation_expiry_failover_when_llm_agrees(mock_storage):
    mock_trans = AsyncMock()
    mock_trans.delete_torrent.return_value = True
    mock_trackers = MagicMock()
    mock_trackers.get_trackers.return_value = []
    mock_trackers.get_health_summary.return_value = {"total_discovered": 0, "healthy_count": 0}
    mock_ollama = AsyncMock(spec=OllamaClient)

    diagnostics = Diagnostics(mock_trans, mock_ollama)

    booster = TorrentBooster(
        transmission=mock_trans,
        tracker_service=mock_trackers,
        storage=mock_storage,
        diagnostics=diagnostics
    )

    rec = StalledRecord(torrent_id="88", torrent_hash="truly_dead")
    rec.state = BoostState.BOOSTING
    rec.grace_period_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    booster.stalled_records["truly_dead"] = rec

    dead_torrent = TorrentInfo(
        id="88",
        hashString="truly_dead",
        name="Truly Dead ISO",
        status="stalledDL",
        percentDone=0.0,
        rateDownload=0.0,
        peersConnected=0,
        peersSendingToUs=0
    )

    await booster.set_auto_failover_enabled(True)
    await booster.set_llm_assisted_failover_enabled(True)

    mock_judgement = TorrentJudgement(
        torrent_id="88",
        torrent_hash="truly_dead",
        torrent_name="Truly Dead ISO",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.DEAD_SWARM,
        viability_score=0.0,
        recommended_action=RecommendedAction.FAILOVER,
        action_explanation="Swarm is completely dead",
        confidence=0.98,
        reasoning="Swarm has 0 seeds and 0 peers across all trackers."
    )
    diagnostics.judge_torrent = AsyncMock(return_value=mock_judgement)

    await booster.run_cycle([dead_torrent])

    # Should proceed with failover
    assert rec.state == BoostState.FAILED_OVER
    mock_trans.delete_torrent.assert_called_once_with("88", delete_files=True)


# ---------------------------------------------------------------------------
# 5. API Endpoints Tests
# ---------------------------------------------------------------------------

def test_api_judge_torrent_endpoint(client):
    mock_judgement = TorrentJudgement(
        torrent_id="5",
        torrent_hash="hash555",
        torrent_name="Fedora Linux",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.HEALTHY,
        viability_score=0.88,
        recommended_action=RecommendedAction.WAIT,
        action_explanation="Swarm is fine",
        confidence=0.92,
        reasoning="Strong seed-to-peer ratio."
    )

    target_torrent = TorrentInfo(
        id="5",
        hashString="hash555",
        name="Fedora Linux",
        status="downloading",
        percentDone=0.5,
        rateDownload=1000
    )

    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.transmission.get_torrents = AsyncMock(return_value=[target_torrent])
        mock_daemon.diagnostics.judge_torrent = AsyncMock(return_value=mock_judgement)
        mock_daemon.booster = None
        mock_daemon.vpn_adapter = None

        resp = client.post(
            "/api/torrents/hash555/judge",
            json={"user_prompt": "Evaluate swarm speed", "force_fresh": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["torrent_hash"] == "hash555"
        assert data["verdict"] == "healthy"
        assert data["viability_score"] == 0.88


def test_api_get_judgement_endpoints(client):
    mock_judgement = TorrentJudgement(
        torrent_id="5",
        torrent_hash="hash555",
        torrent_name="Fedora Linux",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.HEALTHY,
        viability_score=0.88,
        recommended_action=RecommendedAction.WAIT,
        action_explanation="Swarm is fine",
        confidence=0.92,
        reasoning="Strong swarm."
    )

    with patch("torrent_sentinel.api.router.storage.get_latest_judgement", AsyncMock(return_value=mock_judgement)):
        resp = client.get("/api/torrents/hash555/judgement")
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "healthy"

    with patch("torrent_sentinel.api.router.storage.get_judgements_for_torrent", AsyncMock(return_value=[mock_judgement])):
        resp = client.get("/api/torrents/hash555/judgements")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["torrent_hash"] == "hash555"


def test_api_llm_assess_endpoint(client):
    mock_assessment = SwarmAssessment(
        overall_summary="All torrents are active and VPN is unthrottled.",
        vpn_health_verdict="healthy",
        should_rotate_vpn=False,
        vpn_reasoning="VPN is performing well.",
        torrent_judgements=[],
        recommended_actions=[]
    )

    target_torrent = TorrentInfo(
        id="1",
        hashString="h1",
        name="T1",
        status="downloading",
        percentDone=0.1,
        rateDownload=50000
    )

    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.transmission.get_torrents = AsyncMock(return_value=[target_torrent])
        mock_daemon.diagnostics.assess_swarm = AsyncMock(return_value=mock_assessment)
        mock_daemon.vpn_adapter = None
        mock_daemon.booster = None

        resp = client.post("/api/llm/assess")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_summary"] == "All torrents are active and VPN is unthrottled."
        assert data["should_rotate_vpn"] is False


def test_api_llm_chat_endpoint(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.transmission.get_torrents = AsyncMock(return_value=[])
        mock_daemon.vpn_adapter = None
        mock_daemon.booster = None
        mock_daemon.is_vpn_rotation_enabled = AsyncMock(return_value=True)
        mock_daemon.ollama.chat_about_downloads = AsyncMock(return_value="Everything looks great in your queue.")

        resp = client.post("/api/llm/chat", json={"message": "What is stalled?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Everything looks great in your queue."


def test_api_llm_assisted_failover_toggle(client):
    with patch("torrent_sentinel.api.router.daemon_instance") as mock_daemon:
        mock_daemon.booster.set_llm_assisted_failover_enabled = AsyncMock(return_value=False)

        resp = client.post("/api/settings/llm-assisted-failover", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["llm_assisted_failover_enabled"] is False


# ---------------------------------------------------------------------------
# 6. CLI Command Tests
# ---------------------------------------------------------------------------

def test_cli_judge_command():
    mock_judgement = TorrentJudgement(
        torrent_id="12",
        torrent_hash="cli123hash",
        torrent_name="Arch Linux",
        timestamp=datetime.now(timezone.utc),
        verdict=TorrentVerdict.NEEDS_BOOST,
        viability_score=0.55,
        recommended_action=RecommendedAction.BOOST_TRACKERS,
        action_explanation="Inject fresh trackers",
        confidence=0.87,
        reasoning="Few peers found."
    )

    target_torrent = TorrentInfo(
        id="12",
        hashString="cli123hash",
        name="Arch Linux",
        status="downloading",
        percentDone=0.1
    )

    with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[target_torrent])), \
         patch("torrent_sentinel.engine.diagnostics.Diagnostics.judge_torrent", AsyncMock(return_value=mock_judgement)), \
         patch("torrent_sentinel.engine.storage.Storage.record_judgement", AsyncMock()):
        result = runner.invoke(cli_app, ["judge", "cli123hash"])
        assert result.exit_code == 0
        assert "Arch Linux" in result.output
        assert "NEEDS_BOOST" in result.output


def test_cli_assess_command():
    mock_assessment = SwarmAssessment(
        overall_summary="Swarm is healthy.",
        vpn_health_verdict="healthy",
        should_rotate_vpn=False,
        vpn_reasoning="Normal operations.",
        torrent_judgements=[],
        recommended_actions=[]
    )

    target_torrent = TorrentInfo(
        id="12",
        hashString="cli123hash",
        name="Arch Linux",
        status="downloading",
        percentDone=0.1
    )

    with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[target_torrent])), \
         patch("torrent_sentinel.engine.diagnostics.Diagnostics.assess_swarm", AsyncMock(return_value=mock_assessment)), \
         patch("torrent_sentinel.vpn.get_vpn_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.get_current_profile = AsyncMock(return_value=None)
        mock_get_adapter.return_value = mock_adapter

        result = runner.invoke(cli_app, ["assess"])
        assert result.exit_code == 0
        assert "Swarm is healthy." in result.output
        assert "HEALTHY" in result.output


def test_cli_ask_command():
    with patch("torrent_sentinel.clients.transmission.TransmissionClient.get_torrents", AsyncMock(return_value=[])), \
         patch("torrent_sentinel.clients.ollama.OllamaClient.chat_about_downloads", AsyncMock(return_value="No torrents are stalled.")):
        result = runner.invoke(cli_app, ["ask", "Are any downloads stalled?"])
        assert result.exit_code == 0
        assert "No torrents are stalled." in result.output
