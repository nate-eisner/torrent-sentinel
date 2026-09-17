import pytest
import httpx
from unittest.mock import patch, AsyncMock
from torrent_sentinel.models import ServarrType
from torrent_sentinel.clients.servarr import ServarrClient

@pytest.mark.asyncio
async def test_servarr_get_queue_sonarr():
    client = ServarrClient(
        app_type=ServarrType.SONARR,
        base_url="http://127.0.0.1:8989",
        api_key="test-api-key"
    )

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "records": [
            {
                "id": 42,
                "downloadId": "A1B2C3D4E5",
                "title": "Show.S01E01.1080p",
                "status": "downloading",
                "series": {"title": "Test Show"},
                "episode": {"id": 101, "title": "Pilot"}
            }
        ]
    }

    with patch.object(client._client, "get", AsyncMock(return_value=mock_resp)):
        queue = await client.get_queue()
        assert len(queue) == 1
        item, raw = queue[0]
        assert item.download_id == "a1b2c3d4e5"
        assert item.series_title == "Test Show"
        assert item.episode_title == "Pilot"
        assert raw["episode"]["id"] == 101

    await client.close()

@pytest.mark.asyncio
async def test_servarr_remove_and_blocklist():
    client = ServarrClient(
        app_type=ServarrType.RADARR,
        base_url="http://127.0.0.1:7878",
        api_key="test-api-key"
    )

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 200

    with patch.object(client._client, "delete", AsyncMock(return_value=mock_resp)) as mock_del:
        success = await client.remove_and_blocklist(queue_id=123)
        assert success is True
        mock_del.assert_called_once()
        args, kwargs = mock_del.call_args
        assert "/queue/123" in args[0]
        assert kwargs["params"] == {"removeFromClient": "true", "blocklist": "true"}

    await client.close()

@pytest.mark.asyncio
async def test_servarr_trigger_search_sonarr():
    client = ServarrClient(
        app_type=ServarrType.SONARR,
        base_url="http://127.0.0.1:8989",
        api_key="test-api-key"
    )

    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.status_code = 201

    raw_rec = {"episodeId": 505}
    with patch.object(client._client, "post", AsyncMock(return_value=mock_resp)) as mock_post:
        success, cmd = await client.trigger_search_for_record(raw_rec)
        assert success is True
        assert cmd == "EpisodeSearch"
        args, kwargs = mock_post.call_args
        assert "/command" in args[0]
        assert kwargs["json"] == {"name": "EpisodeSearch", "episodeIds": [505]}

    await client.close()
