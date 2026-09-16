import httpx
import json
from typing import Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import OllamaDiagnosis

class OllamaClient:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=settings.OLLAMA_TIMEOUT)
                return response.status_code == 200
        except Exception:
            return False

    async def diagnose_stalled_torrents(self, context: dict) -> Optional[OllamaDiagnosis]:
        if not settings.OLLAMA_ENABLED:
            return None

        async with httpx.AsyncClient() as client:
            prompt = {
                "model": settings.OLLAMA_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a network diagnostic expert. Analyze torrent tracker errors and recommend VPN rotations. "
                            "Your response must be a single JSON object that strictly follows this schema: "
                            '{"should_rotate": boolean, "recommended_location": "string or null", "confidence": float, '
                            '"reasoning": "string", "suggested_action": "string"}'
                        )
                    },
                    {
                        "role": "user",
                        "content": json.dumps(context)
                    }
                ],
                "stream": False,
                "format": "json"
            }

            try:
                response = await client.post(
                    f"{self.base_url}/api/chat", 
                    json=prompt, 
                    timeout=settings.OLLAMA_TIMEOUT
                )
                
                if response.status_code != 200:
                    print(f"Ollama API error: {response.status_code} - {response.text}")
                    return None

                response.raise_for_status()
                
                result = response.json()
                # Log the raw result to see what's happening
                print(f"DEBUG: Raw Ollama response: {json.dumps(result)}")
                
                content = result["message"]["content"]
                
                # Clean up content in case the model wraps it in markdown code blocks
                if content.startswith("```json"):
                    content = content.replace("```json", "").replace("```", "").strip()
                elif content.startswith("```"):
                    content = content.replace("```", "").strip()

                return OllamaDiagnosis.model_validate_json(content)
            except Exception as e:
                print(f"Ollama diagnosis failed: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                return None

    async def generate_debrief(self, event_data: dict) -> str:
        return f"Event Summary: {event_data.get('reason', 'Unknown reason')}"
