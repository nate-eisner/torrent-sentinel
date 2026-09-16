import logging
import httpx
from torrent_sentinel.config import settings

logger = logging.getLogger(__name__)

class IPVerifier:
    def __init__(self):
        self.endpoints = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/all.json"
        ]

    async def get_public_ip(self) -> str:
        logger.debug("Attempting to determine public IP address...")
        async with httpx.AsyncClient() as client:
            for url in self.endpoints:
                try:
                    logger.debug("Querying IP provider: %s", url)
                    response = await client.get(url, timeout=10.0)
                    response.raise_for_status()
                    data = response.json()
                    ip = None
                    if "ip" in data:
                        ip = data["ip"]
                    elif "ip_addr" in data:
                        ip = data["ip_addr"]

                    if ip:
                        logger.info("Current public IP: %s (via %s)", ip, url)
                        return ip
                except Exception as e:
                    logger.debug("Failed to query IP from %s: %s", url, e)
                    continue

        logger.error("Failed to retrieve public IP address from all providers: %s", self.endpoints)
        raise Exception("Failed to retrieve public IP from all providers")

    async def verify_ip_change(self, old_ip: str) -> bool:
        logger.info("Verifying public IP change (previous IP was: %s)...", old_ip)
        try:
            new_ip = await self.get_public_ip()
            changed = (new_ip != old_ip)
            if changed:
                logger.info("IP change confirmed: %s -> %s", old_ip, new_ip)
            else:
                logger.warning("Public IP did not change! Still: %s", new_ip)
            return changed
        except Exception as e:
            logger.error("Could not verify IP change due to error: %s", e)
            return False

    async def get_location_info(self) -> dict:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get("https://ipapi.co/json/", timeout=10.0)
                response.raise_for_status()
                data = response.json()
                logger.debug("GeoIP location response: %s (%s, %s)", data.get("ip"), data.get("city"), data.get("country_name"))
                return data
            except Exception as e:
                logger.debug("Could not retrieve GeoIP metadata from ipapi.co: %s", e)
                return {}
