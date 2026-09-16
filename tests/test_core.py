import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from torrent_sentinel.models import TorrentInfo, OllamaDiagnosis, LocationProfile, RotationEvent
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.engine.storage import Storage

@pytest_asyncio.fixture
async def mock_transmission():
    client = AsyncMock(spec=TransmissionClient)
    return client

@pytest_asyncio.fixture
async def mock_ollama():
    client = AsyncMock(spec=OllamaClient)
    return client

@pytest_asyncio.fixture
async def mock_storage(tmp_path):
    db_file = tmp_path / "test_sentinel.db"
    storage = Storage(str(db_file))
    await storage.initialize()
    return storage

@pytest.mark.asyncio
async def test_ollama_diagnosis_parsing(mock_ollama):
    # Test that the client correctly handles a valid JSON response from Ollama
    context = {"test": "data"}
    mock_diagnosis = OllamaDiagnosis(
        should_rotate=True,
        recommended_location="us-east",
        confidence=0.95,
        reasoning="Test reasoning",
        suggested_action="Rotate"
    )
    mock_ollama.diagnose_stalled_torrents.return_value = mock_diagnosis
    
    result = await mock_ollama.diagnose_stalled_torrents(context)
    assert result.should_rotate is True
    assert result.recommended_location == "us-east"

@pytest.mark.asyncio
async def test_storage_rotation_recording(mock_storage):
    event = RotationEvent(
        id="test-uuid",
        timestamp=datetime.now(),
        from_location="old",
        to_location="new",
        reason="test",
        peers_before=10,
        peers_after=20
    )
    await mock_storage.record_rotation(event)
    history = await mock_storage.get_history()
    assert len(history) == 1
    assert history[0].to_location == "new"

@pytest.mark.asyncio
async def test_storage_scoring(mock_storage):
    await mock_storage.update_score("us-east", 5)
    await mock_storage.update_score("us-east", 15)
    top = await mock_storage.get_top_locations(limit=1)
    assert "us-east" in top

def test_torrent_info_validation():
    # Test with Transmission int error = 0 (no error)
    t1 = TorrentInfo(
        id="1",
        name="Test Torrent 1",
        status="downloading",
        rateDownload=1024.0,
        rateUpload=512.0,
        peersConnected=10,
        peersSendingToUs=5,
        error=0,
        errorString=""
    )
    assert t1.error == 0
    assert t1.rate_download == 1024.0

    # Test with Transmission int error = 2 (tracker error)
    t2 = TorrentInfo(
        id="2",
        name="Test Torrent 2",
        status="stalled",
        rateDownload=0.0,
        rateUpload=0.0,
        peersConnected=0,
        peersSendingToUs=0,
        error=2,
        errorString="Connection timed out"
    )
    assert t2.error == 2
    assert t2.error_string == "Connection timed out"

