import aiosqlite
from datetime import datetime
from typing import List, Optional
from torrent_sentinel.config import settings
from torrent_sentinel.models import RotationEvent, LocationProfile

class Storage:
    def __init__(self, db_path: str = "sentinel.db"):
        self.db_path = db_path

    async def initialize(self):
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
            await db.commit()

    async def record_rotation(self, event: RotationEvent):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO rotation_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.timestamp.isoformat(), event.from_location, 
                 event.to_location, event.reason, event.peers_before, event.peers_after)
            )
            await db.commit()

    async def update_score(self, location_id: str, peer_delta: int):
        async with aiosqlite.connect(self.db_path) as db:
            # Simple moving average/increment logic
            await db.execute("""
                INSERT INTO location_scores (location_id, avg_peers, success_count, last_updated)
                VALUES (?, ?, 1, ?)
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
                return [row[0] for row in rows]

    async def get_history(self) -> List[RotationEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM rotation_events ORDER BY timestamp DESC") as cursor:
                rows = await cursor.fetchall()
                return [RotationEvent(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    from_location=row["from_location"],
                    to_location=row["to_location"],
                    reason=row["reason"],
                    peers_before=row["peers_before"],
                    peers_after=row["peers_after"]
                ) for row in rows]
