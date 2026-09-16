import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from torrent_sentinel.config import settings
from torrent_sentinel.models import RotationEvent

logger = logging.getLogger(__name__)

class BaseNotificationDispatcher(ABC):
    @abstractmethod
    async def send_rotation_event(self, event: RotationEvent):
        pass

    @abstractmethod
    async def send_swarm_recovery(self, torrent_name: str, peer_gain: int, location: str):
        pass

class DiscordDispatcher(BaseNotificationDispatcher):
    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url
        import httpx
        self.client = httpx.AsyncClient()

    async def send_rotation_event(self, event: RotationEvent):
        if not self.webhook_url:
            return

        import datetime
        payload = {
            "embeds": [{
                "title": "🔄 VPN Rotation Executed",
                "color": 3447003, # Blue
                "fields": [
                    {"name": "From", "value": event.from_location, "inline": True},
                    {"name": "To", "value": event.to_location, "inline": True},
                    {"name": "Reason", "value": event.reason, "inline": False},
                    {"name": "Peer Delta", "value": f"{event.peers_after - event.peers_before:+}", "inline": True}
                ],
                "timestamp": event.timestamp.isoformat()
            }]
        }
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(self.webhook_url, json=payload)
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")

    async def send_swarm_recovery(self, torrent_name: str, peer_gain: int, location: str):
        if not self.webhook_url:
            return

        payload = {
            "embeds": [{
                "title": "🚀 Swarm Recovery Detected",
                "color": 3066993, # Green
                "description": f"Torrent **{torrent_name}** picked up **{peer_gain}** new seeds on **{location}**!",
                "timestamp": datetime.datetime.now().isoformat()
            }]
        }
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(self.webhook_url, json=payload)
        except Exception as e:
            logger.error(f"Discord recovery notification failed: {e}")

class TelegramDispatcher(BaseNotificationDispatcher):
    def __init__(self, token: Optional[str], chat_id: Optional[str]):
        self.token = token
        self.chat_id = chat_id

    async def send_rotation_event(self, event: RotationEvent):
        if not self.token or not self.chat_id:
            return
        import httpx
        msg = (f"🔄 *VPN Rotation*\n\n"
               f"From: `{event.from_location}`\n"
               f"To: `{event.to_location}`\n"
               f"Reason: {event.reason}")
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")

    async def send_swarm_recovery(self, torrent_name: str, peer_gain: int, location: str):
        if not self.token or not self.chat_id:
            return
        import httpx
        msg = f"🚀 *Swarm Recovery*\n\n{torrent_name} +{peer_gain} seeds ({location})"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"})
        except Exception as e:
            logger.error(f"Telegram recovery notification failed: {e}")

class NotificationDispatcher:
    def __init__(self):
        self.dispatchers = []
        if settings.DISCORD_WEBHOOK_URL:
            self.dispatchers.append(DiscordDispatcher(settings.DISCORD_WEBHOOK_URL))
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            self.dispatchers.append(TelegramDispatcher(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID))

    async def dispatch_rotation(self, event: RotationEvent):
        import asyncio
        tasks = [d.send_rotation_event(event) for d in self.dispatchers]
        if tasks:
            await asyncio.gather(*tasks)

    async def dispatch_recovery(self, torrent_name: str, peer_gain: int, location: str):
        import asyncio
        tasks = [d.send_swarm_recovery(torrent_name, peer_gain, location) for d in self.dispatchers]
        if tasks:
            await asyncio.gather(*tasks)
