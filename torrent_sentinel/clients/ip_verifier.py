import httpx
from torrent_sentinel.config import settings

class IPVerifier:
    def __init__(self):
        # Using ipify as a reliable, simple endpoint
        self.endpoints = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/all.json"
        ]

    async def get_public_ip(self) -> str:
        async with httpx.AsyncClient() as client:
            for url in self.endpoints:
                try:
                    response = await client.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    # Handle different JSON structures from different providers
                    if "ip" in data:
                        return data["ip"]
                    elif "ip_addr" in data:
                        return data["ip_addr"]
                except Exception:
                    continue
            raise Exception("Failed to retrieve public IP from all providers")

    async def verify_ip_change(self, old_ip: str) -> bool:
        new_ip = await self.get_public_ip()
        return new_ip != old_ip

    async def get_location_info(self) -> dict:
        # Using ipapi.co for geo info (free tier/limited)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get("https://ipapi.co/json/", timeout=10)
                response.raise_for_status()
                return response.json()
            except Exception:
                return {}
