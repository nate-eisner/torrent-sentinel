import os
import subprocess
import logging
from typing import List, Optional
from torrent_sentinel.vpn.base import BaseVPNAdapter
from torrent_sentinel.models import LocationProfile
from torrent_sentinel.config import settings

logger = logging.getLogger(__name__)

class UnraidWireGuardAdapter(BaseVPNAdapter):
    def __init__(self):
        self.configs_dir = settings.VPN_CONFIGS_DIR
        self.interface = settings.VPN_INTERFACE

    async def get_available_locations(self) -> List[LocationProfile]:
        profiles = []
        if not os.path.exists(self.configs_dir):
            return profiles

        for filename in os.listdir(self.configs_dir):
            if filename.endswith(".conf"):
                # Assuming filename format: country-city.conf or similar
                name = filename.replace(".conf", "")
                profiles.append(LocationProfile(
                    id=name,
                    name=name.replace("-", " ").title(),
                    country="Unknown", # In a real impl, we'd parse this or use metadata
                    endpoint="", 
                    config_file=os.path.join(self.configs_dir, filename)
                ))
        return profiles

    async def rotate_to(self, profile: LocationProfile) -> bool:
        try:
            # 1. Stop current tunnel
            subprocess.run(["wg-quick", "down", self.interface], check=False)
            
            # 2. Copy new config to active interface location
            # Note: This assumes the interface is managed by a specific file path
            # In Unraid, this might be /boot/config/wireguard/wg1/wg0.conf or similar
            # For this implementation, we'll simulate the swap by pointing to the new file
            
            # 3. Start new tunnel
            result = subprocess.run(["wg-quick", "up", profile.config_file], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to bring up {profile.config_file}: {result.stderr}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error during WireGuard rotation: {e}")
            return False

    async def get_current_profile(self) -> Optional[LocationProfile]:
        # Implementation would check which config is currently active in the interface
        return None 
