import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional
import datetime
import httpx

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

    async def send_rotation_event(self, event: RotationEvent):
        if not self.webhook_url:
            return

        logger.debug("Dispatching Discord rotation notification...")
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
            async with httpx.AsyncClient() as client:
                res = await client.post(self.webhook_url, json=payload, timeout=10.0)
                if res.status_code in (200, 204):
                    logger.info("Successfully delivered Discord rotation alert.")
                else:
                    logger.warning("Discord webhook responded with HTTP %d: %s", res.status_code, res.text)
        except Exception as e:
            logger.error("Discord notification failed: %s", e)

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
            async with httpx.AsyncClient() as client:
                await client.post(self.webhook_url, json=payload, timeout=10.0)
                logger.info("Delivered Discord swarm recovery alert for %s", torrent_name)
        except Exception as e:
            logger.error("Discord recovery notification failed: %s", e)

class TelegramDispatcher(BaseNotificationDispatcher):
    def __init__(self, token: Optional[str], chat_id: Optional[str]):
        self.token = token
        self.chat_id = chat_id

    async def send_rotation_event(self, event: RotationEvent):
        if not self.token or not self.chat_id:
            return

        logger.debug("Dispatching Telegram rotation notification...")
        msg = (f"🔄 *VPN Rotation*\n\n"
               f"From: `{event.from_location}`\n"
               f"To: `{event.to_location}`\n"
               f"Reason: {event.reason}")
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10.0)
                if res.status_code == 200:
                    logger.info("Successfully delivered Telegram rotation alert.")
                else:
                    logger.warning("Telegram API responded with HTTP %d: %s", res.status_code, res.text)
        except Exception as e:
            logger.error("Telegram notification failed: %s", e)

    async def send_swarm_recovery(self, torrent_name: str, peer_gain: int, location: str):
        if not self.token or not self.chat_id:
            return

        msg = f"🚀 *Swarm Recovery*\n\n{torrent_name} +{peer_gain} seeds ({location})"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10.0)
                logger.info("Delivered Telegram swarm recovery alert for %s", torrent_name)
        except Exception as e:
            logger.error("Telegram recovery notification failed: %s", e)

class NotificationDispatcher:
    def __init__(self):
        self.dispatchers = []
        if settings.DISCORD_WEBHOOK_URL:
            self.dispatchers.append(DiscordDispatcher(settings.DISCORD_WEBHOOK_URL))
            logger.info("Configured Discord notifications.")
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            self.dispatchers.append(TelegramDispatcher(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID))
            logger.info("Configured Telegram notifications.")

    async def dispatch_rotation(self, event: RotationEvent):
        tasks = [d.send_rotation_event(event) for d in self.dispatchers]
        if tasks:
            logger.info("Dispatching rotation notification to %d service(s)...", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)

    async def dispatch_recovery(self, torrent_name: str, peer_gain: int, location: str):
        tasks = [d.send_swarm_recovery(torrent_name, peer_gain, location) for d in self.dispatchers]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
