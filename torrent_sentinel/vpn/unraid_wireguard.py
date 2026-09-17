import os
import shutil
import subprocess
import logging
import socket
import struct
from typing import List, Optional, Tuple
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
        self._default_gw: Optional[str] = None
        self._default_dev: Optional[str] = None
        logger.info(
            "Initialized UnraidWireGuardAdapter: interface=%s, configs_dir=%s, active_config=%s",
            self.interface, self.configs_dir, self.active_config_path
        )
        # Pre-detect default gateway and apply LAN routing rules on adapter initialization
        self._detect_default_gateway()
        self._apply_lan_routing()

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

    def _detect_default_gateway(self) -> Tuple[Optional[str], Optional[str]]:
        """Detects the host/Docker default gateway IP and network interface."""
        if self._default_gw and self._default_dev:
            return self._default_gw, self._default_dev

        # 1. Primary: Parse standard Linux /proc/net/route
        if os.path.exists("/proc/net/route"):
            try:
                with open("/proc/net/route", "r") as f:
                    for line in f:
                        fields = line.strip().split()
                        if len(fields) >= 8 and fields[1] == "00000000" and fields[7] == "00000000":
                            gw_hex = fields[2]
                            if gw_hex != "00000000":
                                dev = fields[0]
                                gw_ip = socket.inet_ntoa(struct.pack("<L", int(gw_hex, 16)))
                                self._default_gw = gw_ip
                                self._default_dev = dev
                                logger.info("Discovered primary gateway from /proc/net/route: %s dev %s", gw_ip, dev)
                                return gw_ip, dev
            except Exception as e:
                logger.debug("Error reading /proc/net/route: %s", e)

        # 2. Fallback: Parse `ip route` output
        for cmd in [
            ["ip", "-4", "route", "show", "table", "main", "default"],
            ["ip", "-4", "route", "show", "default"],
            ["ip", "-4", "route"]
        ]:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and res.stdout:
                    for line in res.stdout.splitlines():
                        if "default via" in line:
                            parts = line.split()
                            via_idx = parts.index("via")
                            gw_ip = parts[via_idx + 1]
                            dev = "eth0"
                            if "dev" in parts:
                                dev = parts[parts.index("dev") + 1]
                            self._default_gw = gw_ip
                            self._default_dev = dev
                            logger.info("Discovered primary gateway from '%s': %s dev %s", " ".join(cmd), gw_ip, dev)
                            return gw_ip, dev
            except Exception:
                continue

        return None, None

    def _apply_lan_routing(self):
        """Applies policy routing rules and routes so LAN traffic to published ports (8000, 9091) can bypass VPN."""
        if not settings.LAN_NETWORK:
            return

        subnets = [s.strip() for s in settings.LAN_NETWORK.split(",") if s.strip()]
        if not subnets:
            return

        logger.info("Applying LAN bypass routing for subnets: %s", subnets)
        gw_ip, dev = self._detect_default_gateway()
        if not gw_ip or not dev:
            logger.warning("Could not determine default gateway/interface; cannot configure explicit LAN bypass routes.")
            return

        try:
            for subnet in subnets:
                # 1. Add explicit route in table main (idempotent replace)
                res_route = subprocess.run(
                    ["ip", "-4", "route", "replace", subnet, "via", gw_ip, "dev", dev],
                    capture_output=True,
                    text=True
                )
                if res_route.returncode == 0:
                    logger.info("Configured LAN route: %s via %s dev %s", subnet, gw_ip, dev)
                else:
                    logger.warning("Failed to configure LAN route for %s via %s: %s", subnet, gw_ip, res_route.stderr.strip())

                # 2. Add policy routing rule to lookup table main with priority 100
                # (Preempts WireGuard's suppress_prefixlength and 51820 table)
                subprocess.run(["ip", "-4", "rule", "del", "to", subnet, "table", "main", "pref", "100"], capture_output=True, text=True)
                res_rule = subprocess.run(["ip", "-4", "rule", "add", "to", subnet, "table", "main", "pref", "100"], capture_output=True, text=True)
                if res_rule.returncode == 0:
                    logger.debug("Configured policy rule for %s -> table main (pref 100)", subnet)

            logger.info("LAN bypass routing successfully applied.")
        except FileNotFoundError:
            logger.debug("Command 'ip' not found; skipping LAN routing configuration.")
        except Exception as e:
            logger.warning("Could not complete LAN bypass routing: %s", e)

    def _fix_rp_filter(self, iface: str):
        """Sets rp_filter=2 (loose mode) on interfaces so the Linux kernel doesn't drop incoming WireGuard packets."""
        for target in ["all", "default", iface, "eth0"]:
            try:
                subprocess.run(["sysctl", "-w", f"net.ipv4.conf.{target}.rp_filter=2"], capture_output=True, text=True)
            except Exception:
                pass

    def _apply_iptables_rules(self, iface: str):
        """Applies TCP MSS clamping and outbound NAT MASQUERADE for the WireGuard interface."""
        try:
            # Clamp MSS to path MTU to prevent large TCP packets (HTTPS/curl) from hanging
            subprocess.run([
                "iptables", "-t", "mangle", "-A", "POSTROUTING",
                "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
                "-j", "TCPMSS", "--clamp-mss-to-pmtu"
            ], capture_output=True, text=True)
            # Masquerade outbound traffic leaving the VPN tunnel
            subprocess.run([
                "iptables", "-t", "nat", "-A", "POSTROUTING",
                "-o", iface, "-j", "MASQUERADE"
            ], capture_output=True, text=True)
        except Exception as e:
            logger.debug("iptables rules setup: %s", e)

    def _ensure_resolv_conf(self):
        """Ensures /etc/resolv.conf has public fallback DNS (1.1.1.1, 8.8.8.8) so hostnames resolve."""
        try:
            content = ""
            if os.path.exists("/etc/resolv.conf"):
                with open("/etc/resolv.conf", "r") as f:
                    content = f.read()

            fallbacks = ["1.1.1.1", "8.8.8.8"]
            missing = [ip for ip in fallbacks if ip not in content]
            if missing:
                with open("/etc/resolv.conf", "a") as f:
                    for ip in missing:
                        f.write(f"\nnameserver {ip}\n")
                logger.info("Appended public DNS fallbacks to /etc/resolv.conf: %s", missing)
        except Exception as e:
            logger.debug("Could not append fallback DNS to /etc/resolv.conf: %s", e)

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

            # 5. Fix reverse path filtering (rp_filter) so incoming WireGuard packets are not dropped
            self._fix_rp_filter(profile.id)

            # 6. Apply TCP MSS clamping and NAT masquerade on wireguard interface
            self._apply_iptables_rules(profile.id)

            # 7. Ensure fallback DNS in /etc/resolv.conf so curl and domain resolution always work
            self._ensure_resolv_conf()

            # 8. Apply LAN bypass routing to ensure WebUIs and local LAN services remain accessible
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
