import logging
from torrent_sentinel.config import settings
from torrent_sentinel.vpn.base import BaseVPNAdapter

logger = logging.getLogger(__name__)

def get_vpn_adapter() -> BaseVPNAdapter:
    """Returns the configured VPN adapter instance."""
    adapter_type = settings.VPN_TYPE.lower().strip()
    if adapter_type in ("wireguard", "wireguard_gateway", "unraid_wireguard"):
        from torrent_sentinel.vpn.unraid_wireguard import UnraidWireGuardAdapter
        logger.info("Instantiating WireGuard Adapter (interface: %s)", settings.VPN_INTERFACE)
        return UnraidWireGuardAdapter()
    elif adapter_type == "mock":
        from torrent_sentinel.vpn.mock import MockVPNAdapter
        logger.info("Instantiating MockVPNAdapter")
        return MockVPNAdapter()
    else:
        logger.warning("Unknown VPN_TYPE '%s', falling back to MockVPNAdapter", settings.VPN_TYPE)
        from torrent_sentinel.vpn.mock import MockVPNAdapter
        return MockVPNAdapter()
