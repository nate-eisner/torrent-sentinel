import logging
import json
import httpx
from typing import Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import OllamaDiagnosis

logger = logging.getLogger(__name__)

class OllamaClient:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    async def health_check(self) -> bool:
        if not settings.OLLAMA_ENABLED:
            logger.info("Ollama integration is disabled by SENTINEL_OLLAMA_ENABLED=false")
            return False

        logger.info("Performing health check against Ollama at %s...", self.base_url)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=min(settings.OLLAMA_TIMEOUT, 10))
                if response.status_code == 200:
                    tags_data = response.json()
                    models = [m.get("name") for m in tags_data.get("models", [])]
                    logger.info("Ollama is reachable! Available models on host: %s", models)
                    if settings.OLLAMA_MODEL not in models and not any(m.startswith(settings.OLLAMA_MODEL) for m in models):
                        logger.warning(
                            "Configured model '%s' was not found in Ollama models list: %s. You may need to run 'ollama pull %s'.",
                            settings.OLLAMA_MODEL, models, settings.OLLAMA_MODEL
                        )
                    return True
                else:
                    logger.warning("Ollama health check returned HTTP %d: %s", response.status_code, response.text)
                    return False
        except httpx.ConnectError as e:
            logger.error("Failed to connect to Ollama at %s: connection refused. Check host IP and port. Error: %s", self.base_url, e)
            return False
        except Exception as e:
            logger.error("Ollama health check failed with unexpected error: %s", e, exc_info=True)
            return False

    async def diagnose_stalled_torrents(self, context: dict) -> Optional[OllamaDiagnosis]:
        if not settings.OLLAMA_ENABLED:
            logger.info("Skipping AI diagnosis: Ollama is disabled in configuration.")
            return None

        stalled_count = len(context.get("stalled_torrents", []))
        logger.info(
            "Invoking Ollama AI diagnosis for %d stalled/slow torrent(s) using model '%s' at %s...",
            stalled_count, settings.OLLAMA_MODEL, self.base_url
        )
        logger.debug("Diagnostics context sent to Ollama: %s", json.dumps(context, indent=2))

        async with httpx.AsyncClient() as client:
            prompt = {
                "model": settings.OLLAMA_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert BitTorrent network diagnostic and WireGuard VPN routing advisor. "
                            "Analyze the status of all active torrents (stuck vs actively downloading, and peer counts) "
                            "to decide whether a full WireGuard VPN gateway location swap is warranted.\n\n"
                            "Decision Guidelines:\n"
                            "1. Swapping VPN locations drops the network tunnel for ALL torrents, interrupting active downloads.\n"
                            "2. An individual torrent booster already handles isolated stuck torrents by injecting verified public trackers and re-announcing.\n"
                            "3. Do NOT recommend rotating VPN (should_rotate = false) if only one or a few torrents are stuck while others are downloading healthily with active peers, or if stuck torrents are newly queued.\n"
                            "4. Recommend rotating VPN (should_rotate = true) if:\n"
                            "   - Actively downloading torrents have very minimal peers across the board (e.g. 0-2 peers each), indicating VPN endpoint throttling or tracker blocking on the current VPN IP.\n"
                            "   - Multiple torrents are stuck and tracker boost attempts have already failed with 0 seeds.\n"
                            "   - Severe tracker errors across multiple torrents indicate IP shadow-banning on the current VPN location.\n\n"
                            "Your response must be a single JSON object that strictly follows this schema:\n"
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
                    logger.error("Ollama API responded with error HTTP %d: %s", response.status_code, response.text)
                    return None

                response.raise_for_status()
                result = response.json()
                logger.debug("Raw Ollama response payload: %s", result)
                
                content = result.get("message", {}).get("content", "")
                
                # Strip code markdown block formatting if emitted by the model
                if content.startswith("```json"):
                    content = content.replace("```json", "").replace("```", "").strip()
                elif content.startswith("```"):
                    content = content.replace("```", "").strip()

                diagnosis = OllamaDiagnosis.model_validate_json(content)
                logger.info(
                    "Ollama AI Decision: should_rotate=%s | confidence=%.2f | recommended_location=%s | action=%s",
                    diagnosis.should_rotate, diagnosis.confidence, diagnosis.recommended_location, diagnosis.suggested_action
                )
                logger.info("Ollama AI Reasoning: %s", diagnosis.reasoning)
                return diagnosis

            except httpx.ConnectError as e:
                logger.error("Could not connect to Ollama service at %s during diagnosis: %s", self.base_url, e)
                return None
            except httpx.TimeoutException as e:
                logger.error("Ollama request timed out after %d seconds: %s", settings.OLLAMA_TIMEOUT, e)
                return None
            except Exception as e:
                logger.error("Failed to parse or execute Ollama diagnosis: %s", e, exc_info=True)
                return None

    async def generate_debrief(self, event_data: dict) -> str:
        return f"Event Summary: {event_data.get('reason', 'Unknown reason')}"
