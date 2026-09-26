import logging
import json
import uuid
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from torrent_sentinel.config import settings
from torrent_sentinel.models import (
    OllamaDiagnosis, 
    TorrentJudgement, 
    TorrentVerdict, 
    RecommendedAction, 
    SwarmAssessment,
    AutopilotPlan,
    AutopilotAction,
    AutopilotMode
)

logger = logging.getLogger(__name__)

def _clean_json_content(content: str) -> str:
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    first_brace = content.find("{")
    last_brace = content.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        content = content[first_brace:last_brace+1]
    return content

class OllamaClient:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None, storage: Optional[Any] = None):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self._configured_model = model or settings.OLLAMA_MODEL
        self._runtime_model: Optional[str] = None
        self.storage = storage

    @property
    def model(self) -> str:
        if self._runtime_model and self._runtime_model.strip():
            return self._runtime_model.strip()
        return self._configured_model

    @model.setter
    def model(self, value: Optional[str]):
        self._runtime_model = value.strip() if value and value.strip() else None

    async def get_active_model(self) -> str:
        if self.storage:
            try:
                db_model = await self.storage.get_ollama_model()
                if db_model and db_model.strip():
                    self._runtime_model = db_model.strip()
                    return self._runtime_model
            except Exception as e:
                logger.warning("Could not read active model from storage: %s", e)
        return self.model

    async def set_active_model(self, model_name: str) -> str:
        cleaned = model_name.strip()
        self.model = cleaned
        if self.storage:
            try:
                await self.storage.set_ollama_model(cleaned)
            except Exception as e:
                logger.error("Failed to persist active model to storage: %s", e)
        return cleaned

    async def get_available_models(self) -> List[str]:
        """Query Ollama /api/tags to list available models on the host."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/api/tags",
                    timeout=min(settings.OLLAMA_TIMEOUT, 10)
                )
                if response.status_code == 200:
                    tags_data = response.json()
                    models = [m.get("name") for m in tags_data.get("models", []) if m.get("name")]
                    return models
                else:
                    logger.warning("Ollama /api/tags returned HTTP %d: %s", response.status_code, response.text)
        except Exception as e:
            logger.warning("Failed to fetch available models from Ollama at %s: %s", self.base_url, e)
        return []

    def _build_options(self) -> dict:
        options = {}
        if settings.OLLAMA_NUM_CTX:
            options["num_ctx"] = int(settings.OLLAMA_NUM_CTX)
        return options

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

    async def judge_single_torrent(self, torrent_data: Dict[str, Any], user_prompt: Optional[str] = None) -> Optional[TorrentJudgement]:
        """Ask LLM to perform a deep diagnostic judgement on an individual torrent download."""
        if not settings.OLLAMA_ENABLED:
            logger.info("Skipping AI judgement: Ollama is disabled in configuration.")
            return None

        t_name = torrent_data.get("name", "Unknown Torrent")
        t_id = str(torrent_data.get("id", ""))
        t_hash = str(torrent_data.get("hash", "")).lower()

        logger.info("Invoking Ollama AI judgement for torrent '%s' (#%s)...", t_name, t_id)
        logger.debug("Torrent data context for judgement: %s", json.dumps(torrent_data, indent=2))

        user_content_parts = [
            f"Please evaluate this torrent download and provide an expert diagnostic verdict:\n",
            json.dumps(torrent_data, indent=2)
        ]
        if user_prompt and user_prompt.strip():
            user_content_parts.append(f"\nUser specific query / instructions: {user_prompt.strip()}")

        active_model = await self.get_active_model()
        prompt = {
            "model": active_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert BitTorrent swarm diagnostic engineer and torrent client advisor.\n"
                        "Your job is to judge a single download based on its progress, speeds, connected peers/seeds, "
                        "tracker responses and error codes, boost history, and swarm health.\n\n"
                        "Key Diagnostic Principles:\n"
                        "- Do NOT simply recommend canceling torrents or rotating VPNs if other remedies are better.\n"
                        "- If a torrent is stuck at >90% (e.g. 99.9%), piece availability or corrupted chunks are common: recommend 'recheck' or 'wait'.\n"
                        "- If a torrent has tracker errors like 'Connection timed out' or network glitches, recommend 'reannounce' or 'boost_trackers'.\n"
                        "- If a torrent has 0 seeds on all trackers and no peers for a long time on an old release, it may be dead: recommend 'failover'.\n"
                        "- If trackers report plenty of seeders (e.g. 20+ seeders) but our client connects to 0 while other torrents on this VPN are also starved, the VPN IP might be blocked: recommend 'rotate_vpn'.\n"
                        "- If the download is proceeding or has active seeds transferring data, recommend 'wait'.\n"
                        "- If there is a disk/client error, recommend 'recheck' or 'manual_action'.\n\n"
                        "Valid verdicts (MUST BE ONE OF): 'healthy', 'slow_progress', 'stalled_waiting', 'needs_boost', 'needs_recheck', 'vpn_throttled', 'dead_swarm', 'client_error'\n"
                        "Valid recommended_actions (MUST BE ONE OF): 'wait', 'boost_trackers', 'recheck', 'reannounce', 'rotate_vpn', 'failover', 'manual_action'\n\n"
                        "Respond ONLY with a valid JSON object matching this schema:\n"
                        "{\n"
                        '  "verdict": "healthy|slow_progress|stalled_waiting|needs_boost|needs_recheck|vpn_throttled|dead_swarm|client_error",\n'
                        '  "viability_score": float (between 0.0 and 1.0, probability the download can finish),\n'
                        '  "recommended_action": "wait|boost_trackers|recheck|reannounce|rotate_vpn|failover|manual_action",\n'
                        '  "action_explanation": "concise 1-sentence action summary",\n'
                        '  "confidence": float (between 0.0 and 1.0),\n'
                        '  "reasoning": "detailed explanation of swarm health, peers, and root cause",\n'
                        '  "tracker_analysis": "brief summary of tracker responses and health"\n'
                        "}"
                    )
                },
                {
                    "role": "user",
                    "content": "\n".join(user_content_parts)
                }
            ],
            "stream": False,
            "format": "json"
        }
        options = self._build_options()
        if options:
            prompt["options"] = options

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=prompt,
                    timeout=settings.OLLAMA_TIMEOUT
                )
                if response.status_code != 200:
                    logger.error("Ollama API error HTTP %d during single torrent judgement: %s", response.status_code, response.text)
                    return None

                result = response.json()
                content = result.get("message", {}).get("content", "")
                cleaned = _clean_json_content(content)
                parsed = json.loads(cleaned)

                # Validate and normalize fields
                verdict_str = parsed.get("verdict", "stalled_waiting").lower()
                try:
                    verdict_val = TorrentVerdict(verdict_str)
                except ValueError:
                    verdict_val = TorrentVerdict.STALLED_WAITING

                action_str = parsed.get("recommended_action", "wait").lower()
                try:
                    action_val = RecommendedAction(action_str)
                except ValueError:
                    action_val = RecommendedAction.WAIT

                judgement = TorrentJudgement(
                    id=str(uuid.uuid4())[:8],
                    torrent_id=t_id,
                    torrent_hash=t_hash,
                    torrent_name=t_name,
                    timestamp=datetime.now(timezone.utc),
                    verdict=verdict_val,
                    viability_score=max(0.0, min(1.0, float(parsed.get("viability_score", 0.5)))),
                    recommended_action=action_val,
                    action_explanation=parsed.get("action_explanation", f"Suggested action: {action_val.value}"),
                    confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.7)))),
                    reasoning=parsed.get("reasoning", "No detailed reasoning provided."),
                    tracker_analysis=parsed.get("tracker_analysis"),
                    user_prompt=user_prompt
                )

                logger.info(
                    "Ollama Judgement for '%s': verdict=%s | viability=%.0f%% | action=%s",
                    t_name, judgement.verdict.value, judgement.viability_score * 100, judgement.recommended_action.value
                )
                return judgement

        except httpx.ConnectError as e:
            logger.error("Could not connect to Ollama at %s for torrent judgement: %s", self.base_url, e)
            return None
        except httpx.TimeoutException as e:
            logger.error("Ollama request timed out after %d seconds during torrent judgement: %s", settings.OLLAMA_TIMEOUT, e)
            return None
        except Exception as e:
            logger.error("Failed to execute or parse Ollama single torrent judgement: %s", e, exc_info=True)
            return None

    async def assess_entire_swarm(self, torrents_data: List[Dict[str, Any]], vpn_context: Dict[str, Any]) -> Optional[SwarmAssessment]:
        """Ask LLM to perform an overall health and VPN evaluation across all active downloads."""
        if not settings.OLLAMA_ENABLED:
            logger.info("Skipping AI swarm assessment: Ollama is disabled in configuration.")
            return None

        logger.info("Invoking Ollama swarm assessment for %d torrent(s)...", len(torrents_data))

        payload = {
            "torrents": torrents_data,
            "vpn_context": vpn_context
        }

        active_model = await self.get_active_model()
        prompt = {
            "model": active_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert BitTorrent network and WireGuard VPN routing advisor.\n"
                        "Analyze the complete queue of torrents and current VPN routing to provide an overarching assessment.\n"
                        "Answer specifically:\n"
                        "1. Is the current WireGuard VPN gateway healthy, or is it throttled/shadow-banned?\n"
                        "2. Should the system rotate the VPN gateway (which interrupts all downloads), or should it avoid rotating?\n"
                        "3. What are the specific judgements and recommended actions for individual stalled or slow torrents?\n\n"
                        "Guidelines:\n"
                        "- Rotating VPN drops connections for ALL torrents. If only 1 torrent is stalled while 3 others are downloading fast, do NOT rotate VPN.\n"
                        "- If stuck torrents have 0 seeds on all trackers worldwide, cycling VPN will not revive them (they need failover or tracker boosting).\n\n"
                        "Respond ONLY with a valid JSON object matching this schema:\n"
                        "{\n"
                        '  "overall_summary": "1-2 sentence executive summary of queue health",\n'
                        '  "vpn_health_verdict": "healthy|likely_throttled|inconclusive",\n'
                        '  "should_rotate_vpn": boolean,\n'
                        '  "vpn_reasoning": "detailed explanation of why VPN rotation is or is not warranted",\n'
                        '  "individual_assessments": [\n'
                        '    {\n'
                        '      "torrent_id": "string",\n'
                        '      "torrent_hash": "string",\n'
                        '      "torrent_name": "string",\n'
                        '      "verdict": "healthy|slow_progress|stalled_waiting|needs_boost|needs_recheck|vpn_throttled|dead_swarm|client_error",\n'
                        '      "viability_score": float,\n'
                        '      "recommended_action": "wait|boost_trackers|recheck|reannounce|rotate_vpn|failover|manual_action",\n'
                        '      "action_explanation": "string",\n'
                        '      "confidence": float,\n'
                        '      "reasoning": "string"\n'
                        '    }\n'
                        '  ],\n'
                        '  "recommended_actions": [\n'
                        '    {"action": "string", "target": "string", "priority": "high|medium|low", "description": "string"}\n'
                        '  ]\n'
                        "}"
                    )
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, indent=2)
                }
            ],
            "stream": False,
            "format": "json"
        }
        options = self._build_options()
        if options:
            prompt["options"] = options

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=prompt,
                    timeout=settings.OLLAMA_TIMEOUT
                )
                if response.status_code != 200:
                    logger.error("Ollama API error HTTP %d during swarm assessment: %s", response.status_code, response.text)
                    return None

                result = response.json()
                content = result.get("message", {}).get("content", "")
                cleaned = _clean_json_content(content)
                parsed = json.loads(cleaned)

                judgements: List[TorrentJudgement] = []
                for item in parsed.get("individual_assessments", []):
                    try:
                        v_val = TorrentVerdict(item.get("verdict", "stalled_waiting").lower())
                    except ValueError:
                        v_val = TorrentVerdict.STALLED_WAITING

                    try:
                        a_val = RecommendedAction(item.get("recommended_action", "wait").lower())
                    except ValueError:
                        a_val = RecommendedAction.WAIT

                    judgements.append(TorrentJudgement(
                        id=str(uuid.uuid4())[:8],
                        torrent_id=str(item.get("torrent_id", "")),
                        torrent_hash=str(item.get("torrent_hash", "")).lower(),
                        torrent_name=str(item.get("torrent_name", "Unknown")),
                        timestamp=datetime.now(timezone.utc),
                        verdict=v_val,
                        viability_score=max(0.0, min(1.0, float(item.get("viability_score", 0.5)))),
                        recommended_action=a_val,
                        action_explanation=item.get("action_explanation", f"Suggested: {a_val.value}"),
                        confidence=max(0.0, min(1.0, float(item.get("confidence", 0.7)))),
                        reasoning=item.get("reasoning", "")
                    ))

                return SwarmAssessment(
                    timestamp=datetime.now(timezone.utc),
                    overall_summary=parsed.get("overall_summary", "Queue assessment completed."),
                    vpn_health_verdict=parsed.get("vpn_health_verdict", "healthy"),
                    should_rotate_vpn=bool(parsed.get("should_rotate_vpn", False)),
                    vpn_reasoning=parsed.get("vpn_reasoning", "No specific VPN rotation reason provided."),
                    torrent_judgements=judgements,
                    recommended_actions=parsed.get("recommended_actions", [])
                )

        except Exception as e:
            logger.error("Failed to execute or parse Ollama swarm assessment: %s", e, exc_info=True)
            return None

    async def chat_about_downloads(self, message: str, context: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> str:
        """Conversational chat about active downloads with live context."""
        if not settings.OLLAMA_ENABLED:
            return "Ollama integration is currently disabled in Torrent Sentinel configuration."

        # Format context cleanly, compacting if long
        ctx_str = json.dumps(context, indent=2)
        if len(ctx_str) > 30000:
            ctx_str = json.dumps(context)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Torrent Sentinel AI Assistant, an expert in BitTorrent swarms, WireGuard VPNs, and download optimization.\n"
                    "You have direct real-time access to the user's active downloads, tracker statuses, and VPN gateway state.\n"
                    "Help the user understand their download health, why certain torrents are slow or stuck, "
                    "whether cycling VPNs or canceling torrents is necessary, and recommend targeted actions.\n"
                    "Be concise, technical when appropriate, and clearly explain your rationale."
                )
            },
            {
                "role": "system",
                "content": f"Current live downloads snapshot:\n{ctx_str}"
            }
        ]

        if history:
            for h in history[-6:]:
                if "role" in h and "content" in h:
                    c = str(h["content"])
                    if len(c) > 2000:
                        c = c[:2000] + "... [truncated]"
                    messages.append({"role": h["role"], "content": c})

        messages.append({"role": "user", "content": message})

        active_model = await self.get_active_model()
        prompt = {
            "model": active_model,
            "messages": messages,
            "stream": False
        }
        options = self._build_options()
        if options:
            prompt["options"] = options

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=prompt,
                    timeout=settings.OLLAMA_TIMEOUT
                )
                if response.status_code != 200:
                    return f"Ollama error HTTP {response.status_code}: {response.text}"
                result = response.json()
                return result.get("message", {}).get("content", "No response generated by model.")
        except Exception as e:
            logger.error("Chat about downloads failed: %s", e)
            return f"Failed to communicate with Ollama: {str(e)}"

    async def diagnose_stalled_torrents(self, context: dict) -> Optional[OllamaDiagnosis]:
        if not settings.OLLAMA_ENABLED:
            logger.info("Skipping AI diagnosis: Ollama is disabled in configuration.")
            return None

        active_model = await self.get_active_model()
        stalled_count = len(context.get("stalled_torrents", []))
        logger.info(
            "Invoking Ollama AI diagnosis for %d stalled/slow torrent(s) using model '%s' at %s...",
            stalled_count, active_model, self.base_url
        )
        logger.debug("Diagnostics context sent to Ollama: %s", json.dumps(context, indent=2))

        async with httpx.AsyncClient() as client:
            prompt = {
                "model": active_model,
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
            options = self._build_options()
            if options:
                prompt["options"] = options

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
                cleaned = _clean_json_content(content)

                diagnosis = OllamaDiagnosis.model_validate_json(cleaned)
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

    async def generate_autopilot_plan(
        self, 
        queue_telemetry: Dict[str, Any], 
        vpn_context: Dict[str, Any],
        mode: AutopilotMode = AutopilotMode.OFF
    ) -> Optional[AutopilotPlan]:
        """Generate an autonomous fleet flight plan balancing VPN health and torrent rescue actions."""
        if not settings.OLLAMA_ENABLED:
            logger.info("Skipping AI autopilot: Ollama is disabled in configuration.")
            return None

        # Strictly bound candidate torrents payload to avoid context window overflow
        bounded_telemetry = dict(queue_telemetry)
        if "torrents" in bounded_telemetry and isinstance(bounded_telemetry["torrents"], list):
            if len(bounded_telemetry["torrents"]) > 25:
                bounded_telemetry["torrents"] = bounded_telemetry["torrents"][:25]

        combined_payload = {
            "vpn_context": vpn_context,
            "queue_telemetry": bounded_telemetry
        }

        logger.info(
            "Invoking Ollama AI Autopilot fleet planning (mode: %s, active: %d, stalled: %d, candidates: %d)...",
            mode.value,
            queue_telemetry.get("total_active", 0),
            queue_telemetry.get("stalled_count", 0),
            len(bounded_telemetry.get("torrents", []))
        )
        logger.debug("Autopilot telemetry payload: %s", json.dumps(combined_payload, indent=2))

        active_model = await self.get_active_model()
        async with httpx.AsyncClient() as client:
            prompt = {
                "model": active_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are the autonomous AI Autopilot for Torrent Sentinel. "
                            "You manage BitTorrent fleet health, WireGuard VPN routing gateways, and Servarr media failovers.\n\n"
                            "Your Primary Directives:\n"
                            "1. Maximize download throughput and completion rates.\n"
                            "2. Minimize disruption: Rotating the VPN tunnel resets connections for ALL active downloads.\n"
                            "3. Precision Rescue: Target stalled or degraded torrents with surgical actions (boost_trackers, reannounce, recheck, failover, or wait).\n"
                            "4. Failover is the LAST resort: Only recommend failover if a torrent is genuinely dead (0 seeds across all trackers, no progress for extended time, low viability) and Servarr can find an alternative. Private torrents must NEVER be failed over or boosted.\n"
                            "5. VPN Health: Only set should_rotate_vpn=true if multiple downloads are simultaneously blocked/throttled or experiencing tracker timeouts characteristic of an IP shadow-ban.\n\n"
                            "Action Types allowed: boost_trackers, reannounce, recheck, failover, wait\n\n"
                            "Output strictly valid JSON matching this schema:\n"
                            "{\n"
                            '  "summary": "High-level flight summary of decisions",\n'
                            '  "vpn_health_verdict": "healthy" | "degraded" | "blocked",\n'
                            '  "should_rotate_vpn": boolean,\n'
                            '  "vpn_reasoning": "Detailed explanation of VPN verdict",\n'
                            '  "preferred_vpn_location": "string or null",\n'
                            '  "actions": [\n'
                            "    {\n"
                            '      "action_type": "boost_trackers" | "reannounce" | "recheck" | "failover" | "wait",\n'
                            '      "target_id": "string torrent ID or hash",\n'
                            '      "target_name": "string name",\n'
                            '      "confidence": float between 0.0 and 1.0,\n'
                            '      "viability_score": float between 0.0 and 1.0,\n'
                            '      "reasoning": "Detailed explanation for this specific action",\n'
                            '      "parameters": {}\n'
                            "    }\n"
                            "  ]\n"
                            "}"
                        )
                    },
                    {
                        "role": "user",
                        "content": json.dumps(combined_payload)
                    }
                ],
                "stream": False,
                "format": "json"
            }
            options = self._build_options()
            if options:
                prompt["options"] = options

            try:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=prompt,
                    timeout=settings.OLLAMA_TIMEOUT
                )

                if response.status_code != 200:
                    logger.error("Ollama Autopilot API returned error HTTP %d: %s", response.status_code, response.text)
                    return None

                response.raise_for_status()
                result = response.json()
                content = result.get("message", {}).get("content", "")
                cleaned = _clean_json_content(content)
                parsed = json.loads(cleaned)

                actions: List[AutopilotAction] = []
                for act in parsed.get("actions", []):
                    raw_type = str(act.get("action_type", "wait")).lower()
                    try:
                        rec_act = RecommendedAction(raw_type)
                    except ValueError:
                        rec_act = RecommendedAction.WAIT

                    actions.append(AutopilotAction(
                        action_type=rec_act,
                        target_id=str(act.get("target_id", "")),
                        target_name=act.get("target_name"),
                        confidence=float(act.get("confidence", 0.8)),
                        viability_score=float(act.get("viability_score", 0.5)),
                        reasoning=act.get("reasoning", ""),
                        parameters=act.get("parameters") or {}
                    ))

                plan = AutopilotPlan(
                    mode=mode,
                    summary=parsed.get("summary", "Autopilot evaluation complete"),
                    vpn_health_verdict=parsed.get("vpn_health_verdict", "healthy"),
                    should_rotate_vpn=bool(parsed.get("should_rotate_vpn", False)),
                    vpn_reasoning=parsed.get("vpn_reasoning", ""),
                    preferred_vpn_location=parsed.get("preferred_vpn_location"),
                    actions=actions
                )

                logger.info(
                    "Autopilot Plan Generated [%s]: %s | VPN Rotate: %s | Actions: %d",
                    plan.id, plan.summary, plan.should_rotate_vpn, len(plan.actions)
                )
                return plan

            except httpx.ConnectError as e:
                logger.error("Could not connect to Ollama service at %s for Autopilot: %s", self.base_url, e)
                return None
            except httpx.TimeoutException as e:
                logger.error("Ollama Autopilot request timed out after %d seconds: %s", settings.OLLAMA_TIMEOUT, e)
                return None
            except Exception as e:
                logger.error("Failed to generate or parse Ollama Autopilot plan: %s", e, exc_info=True)
                return None

    async def generate_debrief(self, event_data: dict) -> str:
        return f"Event Summary: {event_data.get('reason', 'Unknown reason')}"

