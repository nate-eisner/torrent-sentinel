import asyncio
import json
from torrent_sentinel.config import settings
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.models import TorrentInfo

async def test_prompt():
    client = OllamaClient()
    print(f"Using model: {settings.OLLAMA_MODEL}")
    print(f"Target URL: {settings.OLLAMA_BASE_URL}")

    # Create a dummy context that mimics a stalled torrent
    context = {
        "stalled_torrents": [
            {
                "id": "123",
                "name": "Test Torrent",
                "error": "HTTP 403 Forbidden",
                "error_string": "Tracker returned 403",
                "peers_connected": 0,
                "rate_download": 0.0
            }
        ],
        "thresholds": {
            "min_seeds": 2,
            "min_rate_kbps": 15.0
        }
    }

    print("Sending diagnostic prompt...")
    # Using the actual client method which uses /api/chat
    from torrent_sentinel.models import OllamaDiagnosis
    diagnosis = await client.diagnose_stalled_torrents(context)
    
    if diagnosis:
        print("\n--- Diagnosis Result ---")
        print(f"Should Rotate: {diagnosis.should_rotate}")
        print(f"Recommended Location: {diagnosis.recommended_location}")
        print(f"Confidence: {diagnosis.confidence}")
        print(f"Reasoning: {diagnosis.reasoning}")
        print(f"Suggested Action: {diagnosis.suggested_action}")
    else:
        print("Failed to get diagnosis.")

if __name__ == "__main__":
    asyncio.run(test_prompt())
