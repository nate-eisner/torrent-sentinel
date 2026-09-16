import os
import shutil
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
        self.active_config_path = settings.VPN_ACTIVE_CONFIG
        self._current_profile: Optional[LocationProfile] = None
        logger.info(
            "Initialized UnraidWireGuardAdapter: interface=%s, configs_dir=%s, active_config=%s",
            self.interface, self.configs_dir, self.active_config_path
        )

    async def get_available_locations(self) -> List[LocationProfile]:
        profiles = []
        if not os.path.exists(self.configs_dir):
            logger.warning("VPN configs directory does not exist: %s", self.configs_dir)
            return profiles

        logger.debug("Scanning for WireGuard .conf profiles in: %s", self.configs_dir)
        try:
            files = sorted(os.listdir(self.configs_dir))
        except Exception as e:
            logger.error("Failed to read VPN configs directory '%s': %s", self.configs_dir, e)
            return profiles

        for filename in files:
            if filename.endswith(".conf") and filename != "active.conf":
                name = filename[:-5]
                profile = LocationProfile(
                    id=name,
                    name=name.replace("-", " ").replace("_", " ").title(),
                    country="Unknown",
                    endpoint="",
                    config_file=os.path.join(self.configs_dir, filename)
                )
                profiles.append(profile)

        logger.info("Found %d WireGuard profile(s) in %s: %s", len(profiles), self.configs_dir, [p.id for p in profiles])
        return profiles

    async def rotate_to(self, profile: LocationProfile) -> bool:
        logger.info("Starting WireGuard rotation to profile '%s' (%s)...", profile.name, profile.config_file)
        try:
            # 1. Stop current tunnel
            logger.debug("Tearing down existing WireGuard interface: wg-quick down %s", self.interface)
            down_res = subprocess.run(["wg-quick", "down", self.interface], capture_output=True, text=True)
            if down_res.returncode == 0:
                logger.debug("wg-quick down %s succeeded", self.interface)
            else:
                logger.debug("wg-quick down %s completed with exit code %d (may already be down): %s", self.interface, down_res.returncode, down_res.stderr.strip())

            # 2. Copy new config to active location if specified
            if self.active_config_path and self.active_config_path != profile.config_file:
                try:
                    os.makedirs(os.path.dirname(self.active_config_path), exist_ok=True)
                    shutil.copyfile(profile.config_file, self.active_config_path)
                    logger.debug("Copied %s -> %s", profile.config_file, self.active_config_path)
                except Exception as copy_err:
                    logger.warning("Could not copy config to active path %s: %s", self.active_config_path, copy_err)

            # 3. Bring up new tunnel
            target_conf = profile.config_file
            logger.info("Bringing up WireGuard tunnel with profile: %s", target_conf)
            result = subprocess.run(["wg-quick", "up", target_conf], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error("wg-quick up failed (code %d): %s", result.returncode, result.stderr.strip())
                return False

            logger.info("wg-quick up succeeded for profile '%s'. Interface output: %s", profile.name, result.stdout.strip())
            self._current_profile = profile
            return True

        except FileNotFoundError:
            logger.error(
                "Command 'wg-quick' was not found in PATH. Ensure wireguard-tools is installed and container has NET_ADMIN capability."
            )
            return False
        except Exception as e:
            logger.error("Unexpected error during WireGuard rotation: %s", e, exc_info=True)
            return False

    async def get_current_profile(self) -> Optional[LocationProfile]:
        return self._current_profile
