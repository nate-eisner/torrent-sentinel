import os
import logging
import aiosqlite
from datetime import datetime
from typing import List, Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import RotationEvent, LocationProfile, BoostEvent

logger = logging.getLogger(__name__)

class Storage:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # If running in Docker and /app/data exists, store there for volume persistence
            if os.path.exists("/app/data"):
                self.db_path = "/app/data/sentinel.db"
            else:
                self.db_path = "sentinel.db"
        else:
            self.db_path = db_path

    async def initialize(self):
        logger.info("Initializing SQLite database storage at: %s", self.db_path)
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rotation_events (
                    id TEXT PRIMARY KEY,
                    timestamp DATETIME,
                    from_location TEXT,
                    to_location TEXT,
                    reason TEXT,
                    peers_before INTEGER,
                    peers_after INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS location_scores (
                    location_id TEXT PRIMARY KEY,
                    avg_peers REAL,
                    success_count INTEGER,
                    failure_count INTEGER,
                    last_updated DATETIME
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS boost_events (
                    id TEXT PRIMARY KEY,
                    timestamp DATETIME,
                    torrent_id TEXT,
                    torrent_hash TEXT,
                    torrent_name TEXT,
                    action TEXT,
                    details TEXT,
                    servarr_app TEXT,
                    success BOOLEAN
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()
        logger.debug("Database tables verified/created successfully.")

    async def record_rotation(self, event: RotationEvent):
        logger.debug("Recording rotation event %s (%s -> %s)...", event.id, event.from_location, event.to_location)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO rotation_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.timestamp.isoformat(), event.from_location, 
                 event.to_location, event.reason, event.peers_before, event.peers_after)
            )
            await db.commit()

    async def update_score(self, location_id: str, peer_delta: int):
        logger.debug("Updating scoreboard metrics for location '%s' with peer delta %+d...", location_id, peer_delta)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO location_scores (location_id, avg_peers, success_count, failure_count, last_updated)
                VALUES (?, ?, 1, 0, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    avg_peers = (avg_peers + ?) / 2,
                    success_count = success_count + 1,
                    last_updated = ?
            """, (location_id, float(peer_delta), datetime.now().isoformat(), 
                  float(peer_delta), datetime.now().isoformat()))
            await db.commit()

    async def get_top_locations(self, limit: int = 5) -> List[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT location_id FROM location_scores ORDER BY avg_peers DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                results = [row[0] for row in rows]
                logger.debug("Top locations from scoreboard (limit=%d): %s", limit, results)
                return results

    async def get_history(self) -> List[RotationEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM rotation_events ORDER BY timestamp DESC") as cursor:
                rows = await cursor.fetchall()
                events = [RotationEvent(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    from_location=row["from_location"],
                    to_location=row["to_location"],
                    reason=row["reason"],
                    peers_before=row["peers_before"],
                    peers_after=row["peers_after"]
                ) for row in rows]
                logger.debug("Fetched %d historical rotation event(s) from database.", len(events))
                return events

    async def record_boost_event(self, event: BoostEvent):
        logger.debug("Recording boost event %s for torrent '%s' (action: %s)...", event.id, event.torrent_name, event.action)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO boost_events (id, timestamp, torrent_id, torrent_hash, torrent_name, action, details, servarr_app, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.timestamp.isoformat(),
                    event.torrent_id,
                    event.torrent_hash,
                    event.torrent_name,
                    event.action,
                    event.details,
                    event.servarr_app.value if event.servarr_app else None,
                    event.success
                )
            )
            await db.commit()

    async def get_boost_events(self, limit: int = 100) -> List[BoostEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM boost_events ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                events = []
                for row in rows:
                    app_val = row["servarr_app"]
                    app_enum = None
                    if app_val:
                        try:
                            from torrent_sentinel.models import ServarrType
                            app_enum = ServarrType(app_val)
                        except Exception:
                            pass
                    events.append(BoostEvent(
                        id=row["id"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                        torrent_id=row["torrent_id"] or "",
                        torrent_hash=row["torrent_hash"] or "",
                        torrent_name=row["torrent_name"] or "",
                        action=row["action"] or "",
                        details=row["details"] or "",
                        servarr_app=app_enum,
                        success=bool(row["success"])
                    ))
                return events

    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings_kv WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
                return default

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO settings_kv (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = ?
            """, (key, value, value))
            await db.commit()

