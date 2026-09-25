import os
import logging
import aiosqlite
from datetime import datetime
from typing import List, Optional, Dict, Any
from torrent_sentinel.config import settings
from torrent_sentinel.models import (
    RotationEvent, 
    LocationProfile, 
    BoostEvent, 
    TorrentJudgement, 
    TorrentVerdict, 
    RecommendedAction,
    AutopilotEvent,
    AutopilotMode
)


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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS torrent_judgements (
                    id TEXT PRIMARY KEY,
                    torrent_id TEXT,
                    torrent_hash TEXT,
                    torrent_name TEXT,
                    timestamp DATETIME,
                    verdict TEXT,
                    viability_score REAL,
                    recommended_action TEXT,
                    action_explanation TEXT,
                    confidence REAL,
                    reasoning TEXT,
                    tracker_analysis TEXT,
                    user_prompt TEXT
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_judgements_hash ON torrent_judgements(torrent_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_judgements_id ON torrent_judgements(torrent_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_judgements_ts ON torrent_judgements(timestamp)")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS autopilot_events (
                    id TEXT PRIMARY KEY,
                    timestamp DATETIME,
                    plan_id TEXT,
                    mode TEXT,
                    action_type TEXT,
                    target_id TEXT,
                    target_name TEXT,
                    confidence REAL,
                    viability_score REAL,
                    reasoning TEXT,
                    executed BOOLEAN,
                    execution_result TEXT
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_autopilot_events_ts ON autopilot_events(timestamp)")
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

    async def get_scoreboard(self) -> List[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT location_id, avg_peers, success_count FROM location_scores ORDER BY avg_peers DESC") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            return []

    async def get_history(self, limit: Optional[int] = None) -> List[RotationEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM rotation_events ORDER BY timestamp DESC"
            params = ()
            if limit is not None:
                query += " LIMIT ?"
                params = (limit,)
            async with db.execute(query, params) as cursor:
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

    async def get_autopilot_mode(self) -> str:
        val = await self.get_setting("autopilot_mode")
        if val:
            return val
        return settings.AUTOPILOT_MODE

    async def set_autopilot_mode(self, mode: str):
        await self.set_setting("autopilot_mode", mode)

    async def get_ollama_model(self) -> str:
        val = await self.get_setting("ollama_model")
        if val and val.strip():
            return val.strip()
        return settings.OLLAMA_MODEL

    async def set_ollama_model(self, model: str):
        await self.set_setting("ollama_model", model.strip())

    async def record_autopilot_event(self, event: AutopilotEvent):
        logger.debug("Recording autopilot event %s (action: %s, mode: %s)...", event.id, event.action_type, event.mode)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO autopilot_events 
                (id, timestamp, plan_id, mode, action_type, target_id, target_name, confidence, viability_score, reasoning, executed, execution_result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.timestamp.isoformat(),
                    event.plan_id,
                    event.mode,
                    event.action_type,
                    event.target_id,
                    event.target_name,
                    event.confidence,
                    event.viability_score,
                    event.reasoning,
                    event.executed,
                    event.execution_result
                )
            )
            await db.commit()

    async def get_autopilot_events(self, limit: int = 50) -> List[AutopilotEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM autopilot_events ORDER BY timestamp DESC LIMIT ?", 
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                events = []
                for row in rows:
                    events.append(AutopilotEvent(
                        id=row["id"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                        plan_id=row["plan_id"],
                        mode=row["mode"] or "off",
                        action_type=row["action_type"] or "",
                        target_id=row["target_id"],
                        target_name=row["target_name"],
                        confidence=float(row["confidence"] if row["confidence"] is not None else 0.0),
                        viability_score=float(row["viability_score"] if row["viability_score"] is not None else 0.0),
                        reasoning=row["reasoning"] or "",
                        executed=bool(row["executed"]),
                        execution_result=row["execution_result"]
                    ))
                return events

    async def record_judgement(self, judgement: TorrentJudgement):
        logger.debug("Recording LLM judgement %s for '%s' (verdict: %s, action: %s)...", 
                     judgement.id, judgement.torrent_name, judgement.verdict, judgement.recommended_action)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO torrent_judgements 
                (id, torrent_id, torrent_hash, torrent_name, timestamp, verdict, viability_score, 
                 recommended_action, action_explanation, confidence, reasoning, tracker_analysis, user_prompt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    judgement.id,
                    judgement.torrent_id,
                    judgement.torrent_hash.lower(),
                    judgement.torrent_name,
                    judgement.timestamp.isoformat(),
                    judgement.verdict.value,
                    judgement.viability_score,
                    judgement.recommended_action.value,
                    judgement.action_explanation,
                    judgement.confidence,
                    judgement.reasoning,
                    judgement.tracker_analysis,
                    judgement.user_prompt
                )
            )
            await db.commit()

    def _row_to_judgement(self, row: aiosqlite.Row) -> TorrentJudgement:
        return TorrentJudgement(
            id=row["id"],
            torrent_id=row["torrent_id"] or "",
            torrent_hash=row["torrent_hash"] or "",
            torrent_name=row["torrent_name"] or "",
            timestamp=datetime.fromisoformat(row["timestamp"]),
            verdict=TorrentVerdict(row["verdict"]),
            viability_score=float(row["viability_score"] if row["viability_score"] is not None else 0.5),
            recommended_action=RecommendedAction(row["recommended_action"]),
            action_explanation=row["action_explanation"] or "",
            confidence=float(row["confidence"] if row["confidence"] is not None else 0.5),
            reasoning=row["reasoning"] or "",
            tracker_analysis=row["tracker_analysis"],
            user_prompt=row["user_prompt"]
        )

    async def get_latest_judgement(self, torrent_identifier: str) -> Optional[TorrentJudgement]:
        ident = torrent_identifier.lower().strip()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM torrent_judgements 
                WHERE LOWER(torrent_hash) = ? OR torrent_id = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (ident, ident)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_judgement(row)
                return None

    async def get_all_latest_judgements(self) -> Dict[str, TorrentJudgement]:
        """Returns a dict mapping lowercase torrent hash and id to their latest judgement."""
        result: Dict[str, TorrentJudgement] = {}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT tj.* FROM torrent_judgements tj
                INNER JOIN (
                    SELECT torrent_hash, MAX(timestamp) as max_ts
                    FROM torrent_judgements
                    GROUP BY torrent_hash
                ) latest ON tj.torrent_hash = latest.torrent_hash AND tj.timestamp = latest.max_ts
                ORDER BY tj.timestamp DESC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    j = self._row_to_judgement(row)
                    if j.torrent_hash:
                        result[j.torrent_hash.lower()] = j
                    if j.torrent_id:
                        result[j.torrent_id] = j
        return result

    async def get_judgements_for_torrent(self, torrent_identifier: str, limit: int = 10) -> List[TorrentJudgement]:
        ident = torrent_identifier.lower().strip()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM torrent_judgements 
                WHERE LOWER(torrent_hash) = ? OR torrent_id = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (ident, ident, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_judgement(r) for r in rows]


