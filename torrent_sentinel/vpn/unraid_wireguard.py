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

    def _teardown_existing_interfaces(self):
        """Finds and tears down any active WireGuard interfaces."""
        try:
            show_res = subprocess.run(["wg", "show", "interfaces"], capture_output=True, text=True)
            if show_res.returncode == 0 and show_res.stdout.strip():
                for iface in show_res.stdout.strip().split():
                    logger.debug("Tearing down active WireGuard interface: %s", iface)
                    subprocess.run(["wg-quick", "down", iface], capture_output=True, text=True)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Interface teardown check: %s", e)

    def _apply_lan_routing(self):
        """Applies policy routing rules and routes so LAN traffic to published ports (8000, 9091) can bypass VPN."""
        if not settings.LAN_NETWORK:
            return

        subnets = [s.strip() for s in settings.LAN_NETWORK.split(",") if s.strip()]
        if not subnets:
            return

        logger.info("Applying LAN bypass routing for subnets: %s", subnets)
        try:
            # 1. Add policy routing rules for each subnet to lookup table 'main' with priority 100
            # (wg-quick adds lookup 51820 at priority ~32764, so priority 100 takes precedence)
            for subnet in subnets:
                subprocess.run(["ip", "-4", "rule", "del", "to", subnet, "table", "main", "pref", "100"], capture_output=True, text=True)
                res = subprocess.run(["ip", "-4", "rule", "add", "to", subnet, "table", "main", "pref", "100"], capture_output=True, text=True)
                if res.returncode == 0:
                    logger.debug("Added ip rule to table main for %s", subnet)

            # 2. Add explicit route via eth0 default gateway in table main if available
            gw_proc = subprocess.run(["ip", "route", "show", "dev", "eth0"], capture_output=True, text=True)
            if gw_proc.returncode == 0:
                for line in gw_proc.stdout.splitlines():
                    if line.startswith("default via"):
                        parts = line.split()
                        if len(parts) >= 3:
                            gw_ip = parts[2]
                            for subnet in subnets:
                                subprocess.run(["ip", "route", "del", subnet, "via", gw_ip, "dev", "eth0"], capture_output=True, text=True)
                                subprocess.run(["ip", "route", "add", subnet, "via", gw_ip, "dev", "eth0"], capture_output=True, text=True)
                            logger.debug("Added explicit LAN routes via eth0 gateway %s", gw_ip)
                        break
        except FileNotFoundError:
            logger.debug("Command 'ip' not found; skipping LAN routing configuration.")
        except Exception as e:
            logger.warning("Could not complete LAN bypass routing: %s", e)

    async def rotate_to(self, profile: LocationProfile) -> bool:
        logger.info("Starting WireGuard rotation to profile '%s' (%s)...", profile.name, profile.config_file)
        try:
            # 1. Stop current/previous tunnels
            self._teardown_existing_interfaces()

            if self._current_profile and self._current_profile.config_file != profile.config_file:
                logger.debug("Tearing down previous WireGuard profile: %s", self._current_profile.config_file)
                subprocess.run(["wg-quick", "down", self._current_profile.config_file], capture_output=True, text=True)

            if self.interface:
                logger.debug("Tearing down configured interface: %s", self.interface)
                subprocess.run(["wg-quick", "down", self.interface], capture_output=True, text=True)

            # Ensure target config interface is not already partially up
            subprocess.run(["wg-quick", "down", profile.config_file], capture_output=True, text=True)

            # 2. Copy new config to active location if specified
            if self.active_config_path and self.active_config_path != profile.config_file:
                try:
                    os.makedirs(os.path.dirname(self.active_config_path), exist_ok=True)
                    shutil.copyfile(profile.config_file, self.active_config_path)
                    logger.debug("Copied %s -> %s", profile.config_file, self.active_config_path)
                except Exception as copy_err:
                    logger.warning("Could not copy config to active path %s: %s", self.active_config_path, copy_err)

            # 3. Secure file permissions if possible to silence warning
            try:
                os.chmod(profile.config_file, 0o600)
            except Exception:
                pass

            # 4. Bring up new tunnel
            target_conf = profile.config_file
            logger.info("Bringing up WireGuard tunnel with profile: %s", target_conf)
            result = subprocess.run(["wg-quick", "up", target_conf], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error("wg-quick up failed (code %d): %s", result.returncode, result.stderr.strip())
                return False

            logger.info("wg-quick up succeeded for profile '%s'. Interface output: %s", profile.name, result.stdout.strip())
            self._current_profile = profile

            # 5. Apply LAN bypass routing to ensure WebUIs remain accessible
            self._apply_lan_routing()
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
        if self._current_profile is not None:
            return self._current_profile

        # Check if an active WireGuard interface is already running
        try:
            show_res = subprocess.run(["wg", "show", "interfaces"], capture_output=True, text=True)
            if show_res.returncode == 0 and show_res.stdout.strip():
                active_ifaces = show_res.stdout.strip().split()
                if active_ifaces:
                    locations = await self.get_available_locations()
                    for loc in locations:
                        if loc.id in active_ifaces:
                            self._current_profile = loc
                            return loc
        except Exception:
            pass

        return None

WireGuardGatewayAdapter = UnraidWireGuardAdapter
