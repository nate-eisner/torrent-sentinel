import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from torrent_sentinel.config import settings
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.api.router import app
from torrent_sentinel.cli import app as cli_app

runner = CliRunner()

@pytest.fixture
def api_client():
    return TestClient(app)

@pytest_asyncio.fixture
async def temp_storage(tmp_path):
    db_file = tmp_path / "test_runtime_model.db"
    storage = Storage(str(db_file))
    await storage.initialize()
    return storage


# ---------------------------------------------------------------------------
# 1. Storage Persistence Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_storage_ollama_model_fallback(temp_storage):
    # If no model set, returns settings.OLLAMA_MODEL
    model = await temp_storage.get_ollama_model()
    assert model == settings.OLLAMA_MODEL

@pytest.mark.asyncio
async def test_storage_ollama_model_persistence(temp_storage):
    await temp_storage.set_ollama_model("llama3.2:3b")
    model = await temp_storage.get_ollama_model()
    assert model == "llama3.2:3b"

    # Overwrite
    await temp_storage.set_ollama_model("mistral:7b-instruct")
    model2 = await temp_storage.get_ollama_model()
    assert model2 == "mistral:7b-instruct"


# ---------------------------------------------------------------------------
# 2. OllamaClient Dynamic Model & Tags Retrieval Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_client_active_model_with_storage(temp_storage):
    client = OllamaClient(storage=temp_storage)
    
    # Defaults to settings.OLLAMA_MODEL
    active = await client.get_active_model()
    assert active == settings.OLLAMA_MODEL

    # Set new model via client
    await client.set_active_model("gemma2:27b")
    assert client.model == "gemma2:27b"
    assert await client.get_active_model() == "gemma2:27b"

    # Verify persisted in storage
    stored = await temp_storage.get_ollama_model()
    assert stored == "gemma2:27b"

    # New client instance with same storage reads persisted model
    new_client = OllamaClient(storage=temp_storage)
    assert await new_client.get_active_model() == "gemma2:27b"

@pytest.mark.asyncio
async def test_ollama_client_get_available_models():
    client = OllamaClient(base_url="http://mock-ollama:11434")

    mock_tags = {
        "models": [
            {"name": "llama3.1:8b", "size": 4700000000},
            {"name": "gemma2:27b", "size": 16000000000},
            {"name": "qwen2.5:32b", "size": 19000000000}
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tags

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        models = await client.get_available_models()
        assert models == ["llama3.1:8b", "gemma2:27b", "qwen2.5:32b"]

@pytest.mark.asyncio
async def test_ollama_client_get_available_models_error():
    client = OllamaClient(base_url="http://mock-ollama:11434")

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        models = await client.get_available_models()
        assert models == []


# ---------------------------------------------------------------------------
# 3. REST API Endpoints Tests
# ---------------------------------------------------------------------------

def test_api_get_llm_models(api_client):
    mock_models = ["gemma4:26b", "llama3.1:8b", "phi3:mini"]

    with patch("torrent_sentinel.clients.ollama.OllamaClient.get_available_models", AsyncMock(return_value=mock_models)):
        res = api_client.get("/api/llm/models")
        assert res.status_code == 200
        data = res.json()
        assert "current_model" in data
        assert "available_models" in data
        assert "llama3.1:8b" in data["available_models"]

def test_api_set_llm_model(api_client):
    with patch("torrent_sentinel.clients.ollama.OllamaClient.get_available_models", AsyncMock(return_value=["llama3.1:8b", "gemma2:9b"])):
        res = api_client.post("/api/llm/model", json={"model": "gemma2:9b"})
        assert res.status_code == 200
        data = res.json()
        assert data["current_model"] == "gemma2:9b"
        assert "gemma2:9b" in data["available_models"]

    # Verify reflected in /api/status
    status_res = api_client.get("/api/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["ollama_model"] == "gemma2:9b"

def test_api_set_llm_model_empty(api_client):
    res = api_client.post("/api/llm/model", json={"model": "   "})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# 4. CLI Commands Tests
# ---------------------------------------------------------------------------

def test_cli_model_status():
    with patch("torrent_sentinel.clients.ollama.OllamaClient.get_available_models", AsyncMock(return_value=["llama3.1:8b", "gemma4:26b"])):
        result = runner.invoke(cli_app, ["model", "status"])
        assert result.exit_code == 0
        assert "Sentinel AI Model Status" in result.stdout
        assert "Active Runtime Model" in result.stdout
        assert "llama3.1:8b" in result.stdout

def test_cli_model_set():
    result = runner.invoke(cli_app, ["model", "set", "mistral-nemo:12b"])
    assert result.exit_code == 0
    assert "Active runtime model successfully set to" in result.stdout
    assert "mistral-nemo:12b" in result.stdout

def test_favicon_endpoints(api_client):
    res_ico = api_client.get("/favicon.ico")
    assert res_ico.status_code == 200
    assert "image/x-icon" in res_ico.headers.get("content-type", "")

    res_svg = api_client.get("/static/favicon.svg")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers.get("content-type", "")

