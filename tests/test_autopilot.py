import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from torrent_sentinel.models import (
    TorrentInfo,
    AutopilotMode,
    AutopilotAction,
    AutopilotPlan,
    AutopilotEvent,
    RecommendedAction,
    LocationProfile,
    RotationEvent
)
from torrent_sentinel.config import settings
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.engine.autopilot import AutopilotEngine
from torrent_sentinel.engine.booster import TorrentBooster, StalledRecord
from torrent_sentinel.engine.decision import DecisionEngine
from torrent_sentinel.vpn.mock import MockVPNAdapter
from torrent_sentinel.api.router import app
from torrent_sentinel.cli import app as cli_app

runner = CliRunner()

@pytest.fixture
def api_client():
    return TestClient(app)

@pytest_asyncio.fixture
async def temp_storage(tmp_path):
    db_file = tmp_path / "test_autopilot.db"
    storage = Storage(str(db_file))
    await storage.initialize()
    return storage

# ---------------------------------------------------------------------------
# 1. Models & Storage Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_storage_crud(temp_storage):
    # Test mode default and set
    default_mode = await temp_storage.get_autopilot_mode()
    assert default_mode in ("off", "advisory", "full")

    await temp_storage.set_autopilot_mode("advisory")
    mode = await temp_storage.get_autopilot_mode()
    assert mode == "advisory"

    # Test event recording and retrieval
    ev = AutopilotEvent(
        plan_id="plan-1",
        mode="advisory",
        action_type="boost_trackers",
        target_id="101",
        target_name="Test Torrent",
        confidence=0.92,
        viability_score=0.75,
        reasoning="Testing event recording",
        executed=False,
        execution_result="Simulated in advisory mode"
    )
    await temp_storage.record_autopilot_event(ev)

    events = await temp_storage.get_autopilot_events(limit=10)
    assert len(events) == 1
    assert events[0].target_name == "Test Torrent"
    assert events[0].confidence == 0.92
    assert events[0].executed is False

# ---------------------------------------------------------------------------
# 2. Ollama Client Autopilot Plan Generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_generate_autopilot_plan():
    client = OllamaClient(base_url="http://mock-ollama:11434", model="test-model")

    mock_llm_response = {
        "message": {
            "content": json.dumps({
                "summary": "Swarm is slightly degraded; 1 torrent stalled.",
                "vpn_health_verdict": "healthy",
                "should_rotate_vpn": False,
                "vpn_reasoning": "Active downloads are fast; isolated torrent issue.",
                "preferred_vpn_location": None,
                "actions": [
                    {
                        "action_type": "boost_trackers",
                        "target_id": "1",
                        "target_name": "Ubuntu Linux",
                        "confidence": 0.88,
                        "viability_score": 0.65,
                        "reasoning": "Stalled with 0 seeds; needs public tracker injection.",
                        "parameters": {}
                    }
                ]
            })
        }
    }

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_llm_response
        mock_post.return_value = mock_resp

        plan = await client.generate_autopilot_plan(
            queue_telemetry={"total_active": 1, "stalled_count": 1},
            vpn_context={"current_location": {"id": "us-ny", "name": "New York"}},
            mode=AutopilotMode.ADVISORY
        )

        assert plan is not None
        assert plan.mode == AutopilotMode.ADVISORY
        assert plan.should_rotate_vpn is False
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == RecommendedAction.BOOST_TRACKERS
        assert plan.actions[0].confidence == 0.88

# ---------------------------------------------------------------------------
# 3. AutopilotEngine Logic & Guardrails
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_autopilot(temp_storage):
    mock_transmission = AsyncMock()
    mock_booster = AsyncMock()
    mock_booster.stalled_records = {}
    mock_decision = AsyncMock()
    mock_vpn = MockVPNAdapter()
    mock_ollama = AsyncMock()
    mock_notifications = AsyncMock()

    engine = AutopilotEngine(
        transmission=mock_transmission,
        booster=mock_booster,
        decision_engine=mock_decision,
        vpn_adapter=mock_vpn,
        storage=temp_storage,
        ollama=mock_ollama,
        notifications=mock_notifications
    )
    return engine, mock_transmission, mock_booster, mock_decision, mock_vpn, mock_ollama

@pytest.mark.asyncio
async def test_autopilot_advisory_mode_no_mutation(setup_autopilot, temp_storage):
    engine, mock_trans, mock_booster, mock_decision, mock_vpn, mock_ollama = setup_autopilot
    await temp_storage.set_autopilot_mode("advisory")

    torrents = [
        TorrentInfo(id="1", hash="hash1", name="Stalled Torrent", progress=0.1, rate_download=0.0, status="downloading", peers_connected=0)
    ]

    mock_ollama.generate_autopilot_plan.return_value = AutopilotPlan(
        mode=AutopilotMode.ADVISORY,
        summary="Test Advisory Plan",
        vpn_health_verdict="healthy",
        should_rotate_vpn=False,
        actions=[
            AutopilotAction(
                action_type=RecommendedAction.BOOST_TRACKERS,
                target_id="1",
                target_name="Stalled Torrent",
                confidence=0.90,
                viability_score=0.6,
                reasoning="Needs boost"
            )
        ]
    )

    plan = await engine.run_autopilot_cycle(torrents=torrents, force=True)
    assert plan is not None
    assert plan.mode == AutopilotMode.ADVISORY
    assert plan.actions[0].executed is False

    # Booster should NOT have been called in advisory mode
    mock_booster.manual_boost.assert_not_called()

    # Event should be logged to storage
    events = await temp_storage.get_autopilot_events()
    assert len(events) == 1
    assert events[0].executed is False

@pytest.mark.asyncio
async def test_autopilot_full_mode_execution(setup_autopilot, temp_storage):
    engine, mock_trans, mock_booster, mock_decision, mock_vpn, mock_ollama = setup_autopilot
    await temp_storage.set_autopilot_mode("full")

    mock_booster.manual_boost.return_value = True

    torrents = [
        TorrentInfo(id="1", hash="hash1", name="Stalled Torrent", progress=0.1, rate_download=0.0, status="downloading", peers_connected=0)
    ]

    mock_ollama.generate_autopilot_plan.return_value = AutopilotPlan(
        mode=AutopilotMode.FULL,
        summary="Test Full Autopilot Plan",
        vpn_health_verdict="healthy",
        should_rotate_vpn=False,
        actions=[
            AutopilotAction(
                action_type=RecommendedAction.BOOST_TRACKERS,
                target_id="1",
                target_name="Stalled Torrent",
                confidence=0.90,
                viability_score=0.6,
                reasoning="Needs boost"
            )
        ]
    )

    plan = await engine.run_autopilot_cycle(torrents=torrents, force=True)
    assert plan is not None
    assert plan.actions[0].executed is True

    # Booster SHOULD have been called in full mode
    mock_booster.manual_boost.assert_called_once_with("1")

    # Event logged as executed
    events = await temp_storage.get_autopilot_events()
    assert len(events) == 1
    assert events[0].executed is True

@pytest.mark.asyncio
async def test_guardrail_private_torrent_immunity(setup_autopilot, temp_storage):
    engine, mock_trans, mock_booster, mock_decision, mock_vpn, mock_ollama = setup_autopilot
    await temp_storage.set_autopilot_mode("full")

    private_torrent = TorrentInfo(
        id="1", hash="hash1", name="Private Linux", progress=0.2, rate_download=0.0,
        status="downloading", peers_connected=0, is_private=True
    )

    mock_ollama.generate_autopilot_plan.return_value = AutopilotPlan(
        mode=AutopilotMode.FULL,
        summary="LLM hallucinated boost on private torrent",
        actions=[
            AutopilotAction(
                action_type=RecommendedAction.BOOST_TRACKERS,
                target_id="1",
                target_name="Private Linux",
                confidence=0.95,
                viability_score=0.5,
                reasoning="Attempting boost"
            )
        ]
    )

    plan = await engine.run_autopilot_cycle(torrents=[private_torrent], force=True)
    assert plan is not None
    # Guardrail must suppress BOOST_TRACKERS to WAIT
    assert plan.actions[0].action_type == RecommendedAction.WAIT
    assert any("private" in g.lower() for g in plan.guardrails_applied)
    mock_booster.manual_boost.assert_not_called()

@pytest.mark.asyncio
async def test_guardrail_failover_confidence_floor(setup_autopilot, temp_storage):
    engine, mock_trans, mock_booster, mock_decision, mock_vpn, mock_ollama = setup_autopilot
    await temp_storage.set_autopilot_mode("full")

    torrent = TorrentInfo(
        id="2", hash="hash2", name="Public Stalled", progress=0.1, rate_download=0.0,
        status="downloading", peers_connected=0
    )

    # Confidence 0.60 is below 0.85 threshold
    mock_ollama.generate_autopilot_plan.return_value = AutopilotPlan(
        mode=AutopilotMode.FULL,
        summary="Low confidence failover test",
        actions=[
            AutopilotAction(
                action_type=RecommendedAction.FAILOVER,
                target_id="2",
                target_name="Public Stalled",
                confidence=0.60,
                viability_score=0.1,
                reasoning="Looks dead maybe"
            )
        ]
    )

    plan = await engine.run_autopilot_cycle(torrents=[torrent], force=True)
    assert plan is not None
    # Guardrail must suppress FAILOVER to WAIT
    assert plan.actions[0].action_type == RecommendedAction.WAIT
    assert any("confidence" in g.lower() for g in plan.guardrails_applied)
    mock_booster.manual_failover.assert_not_called()

@pytest.mark.asyncio
async def test_guardrail_vpn_rotation_cooldown(setup_autopilot, temp_storage):
    engine, mock_trans, mock_booster, mock_decision, mock_vpn, mock_ollama = setup_autopilot
    await temp_storage.set_autopilot_mode("full")

    # Record a rotation event 2 minutes ago
    recent_rot = RotationEvent(
        id="rot-1",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=2),
        from_location="us-ny",
        to_location="us-la",
        reason="Test rotation",
        peers_before=10,
        peers_after=12
    )
    await temp_storage.record_rotation(recent_rot)

    mock_ollama.generate_autopilot_plan.return_value = AutopilotPlan(
        mode=AutopilotMode.FULL,
        summary="Rotate VPN requested",
        vpn_health_verdict="degraded",
        should_rotate_vpn=True,
        vpn_reasoning="Multiple stalled torrents",
        actions=[]
    )

    plan = await engine.run_autopilot_cycle(torrents=[], force=True)
    assert plan is not None
    # Guardrail must suppress VPN rotation
    assert plan.should_rotate_vpn is False
    assert any("cooldown" in g.lower() for g in plan.guardrails_applied)
    mock_decision.execute_rotation.assert_not_called()

# ---------------------------------------------------------------------------
# 4. API Endpoints
# ---------------------------------------------------------------------------

def test_api_autopilot_routes():
    from torrent_sentinel.api.router import storage as router_storage
    asyncio.run(router_storage.initialize())
    with TestClient(app) as client:
        # GET status
        resp = client.get("/api/autopilot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data

        # POST mode change
        resp = client.post("/api/autopilot/mode", json={"mode": "advisory"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "advisory"

        # GET events
        resp = client.get("/api/autopilot/events")
        assert resp.status_code == 200
        assert "events" in resp.json()

        # GET main status includes autopilot_mode
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        assert "autopilot_mode" in status_resp.json()

# ---------------------------------------------------------------------------
# 5. CLI Commands
# ---------------------------------------------------------------------------

def test_cli_autopilot_commands():
    storage = Storage()
    asyncio.run(storage.initialize())

    # Test status
    res = runner.invoke(cli_app, ["autopilot", "status"])
    assert res.exit_code == 0
    assert "AI Autopilot Status" in res.output

    # Test mode change
    res = runner.invoke(cli_app, ["autopilot", "mode", "advisory"])
    assert res.exit_code == 0
    assert "ADVISORY" in res.output
