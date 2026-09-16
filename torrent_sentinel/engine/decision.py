import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from torrent_sentinel.config import settings
from torrent_sentinel.models import RotationEvent, LocationProfile
from torrent_sentinel.clients.transmission import TransmissionClient
from torrent_sentinel.clients.ollama import OllamaClient
from torrent_sentinel.clients.ip_verifier import IPVerifier
from torrent_sentinel.engine.diagnostics import Diagnostics
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.vpn.base import BaseVPNAdapter

logger = logging.getLogger(__name__)

class DecisionEngine:
    def __init__(self, diagnostics: Diagnostics, storage: Storage, vpn_adapter: BaseVPNAdapter):
        self.diagnostics = diagnostics
        self.storage = storage
        self.vpn_adapter = vpn_adapter
        self.transmission = TransmissionClient() # Re-instantiate or pass in

    async def decide_rotation(self, current_torrents) -> Optional[LocationProfile]:
        # 1. Get Diagnosis
        diagnosis = await self.diagnostics.analyze_torrents(current_torrents)
        if not diagnosis or not diagnosis.should_rotate:
            return None

        # 2. Check Anti-Flapping (Simplified for now)
        # In a real impl, we'd check the DB/Memory for recent rotation timestamps

        # 3. Determine Target Location
        # Combine Ollama recommendation with historical performance
        recommended_id = diagnosis.recommended_location
        top_locations = await self.storage.get_top_locations(limit=3)
        
        target_profile = None
        if recommended_id:
            # Try to find the profile object for the recommended ID
            available = await self.vpn_adapter.get_available_locations()
            target_profile = next((p for p in available if p.id == recommended_id), None)
        
        if not target_profile and top_locations:
            available = await self.vpn_adapter.get_available_locations()
            target_profile = next((p for p in available if p.id == top_locations[0]), None)

        return target_profile

    async def execute_rotation(self, from_profile: Optional[LocationProfile], to_profile: LocationProfile, reason: str):
        logger.info(f"Executing rotation: {from_profile.name if from_profile else 'None'} -> {to_profile.name}")
        
        # 1. Optional: Pause torrents (omitted for brevity)

        # 2. Rotate VPN
        success = await self.vpn_adapter.rotate_to(to_profile)
        if not success:
            logger.error("VPN rotation failed!")
            return False

        # 3. Verify IP Change
        verifier = IPVerifier()
        # Wait for interface to settle
        await asyncio.sleep(5) 
        if not await verifier.verify_ip_change("old_ip_placeholder"): # In real impl, track old IP
             logger.warning("IP change not verified, but proceeding...")

        # 4. Reannounce
        # We'll assume we want to reannounce all torrents that were stalled
        await self.transmission.reannounce_torrents(["0"]) # Simplified: reannounce all

        # 5. Record Event
        event = RotationEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            from_location=from_profile.name if from_profile else "None",
            to_location=to_profile.name,
            reason=reason,
            peers_before=0, # Would be captured from diagnostics
            peers_after=0   # Would be captured after grace period
        )
        await self.storage.record_rotation(event)
        
        return True

class SentinelDaemon:
    def __init__(self, vpn_adapter: BaseVPNAdapter):
        self.transmission = TransmissionClient()
        self.ollama = OllamaClient()
        self.storage = Storage()
        self.diagnostics = Diagnostics(self.transmission, self.ollama)
        self.decision_engine = DecisionEngine(self.diagnostics, self.storage, vpn_adapter)
        self.vpn_adapter = vpn_adapter
        self.running = False

    async def run(self):
        await self.storage.initialize()
        self.running = True
        logger.info("Torrent Sentinel Daemon started.")

        while self.running:
            try:
                # 1. Monitor
                torrents = await self.transmission.get_torrents()
                current_profile = await self.vpn_adapter.get_current_profile()

                # 2. Decide
                target_profile = await self.decision_engine.decide_rotation(torrents)

                # 3. Act
                if target_profile:
                    reason = "AI recommended rotation" # In real impl, get from diagnosis.reasoning
                    success = await self.decision_engine.execute_rotation(current_profile, target_profile, reason)
                    if success:
                        logger.info(f"Successfully rotated to {target_profile.name}")
                    else:
                        logger.error("Rotation failed")

                # 4. Wait for next interval
                await asyncio.sleep(60) # Poll every minute

            except Exception as e:
                logger.exception(f"Error in daemon loop: {e}")
                await asyncio.sleep(30)

    def stop(self):
        self.running = False
