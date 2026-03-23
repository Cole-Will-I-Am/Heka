"""
Heka's memory — episodic, semantic, and procedural.

All memory persists in SQLite. Heka never forgets unless it
chooses to — and even then, it archives rather than deletes.
"""

import json
import logging
import time
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger("heka.memory")


class Memory:
    """
    Three-layer memory:
    - Episodic: What happened (events, cycles, outcomes)
    - Semantic: What things mean (learned facts, patterns, opinions)
    - Procedural: How to do things (successful strategies, failure patterns)
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._pending_writes = 0
        self._last_commit = time.time()
        self._commit_every = 8
        self._init_db()

    def _init_db(self):
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS episodic (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                data TEXT NOT NULL,
                significance REAL DEFAULT 0.5,
                timestamp REAL NOT NULL,
                cycle INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS semantic (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.5,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS procedural (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                context TEXT NOT NULL,
                outcome TEXT NOT NULL,
                success INTEGER NOT NULL,
                learned_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episodic_event ON episodic(event);
            CREATE INDEX IF NOT EXISTS idx_episodic_time ON episodic(timestamp);
            CREATE INDEX IF NOT EXISTS idx_procedural_success ON procedural(success);
        """)
        self._conn.commit()

    def _maybe_commit(self, force: bool = False):
        if not self._conn:
            return
        now = time.time()
        if force or self._pending_writes >= self._commit_every or (now - self._last_commit) >= 2.0:
            self._conn.commit()
            self._pending_writes = 0
            self._last_commit = now

    async def store_episodic(self, event: str, data: dict,
                             significance: float = 0.5, cycle: int = 0):
        self._conn.execute(
            "INSERT INTO episodic (event, data, significance, timestamp, cycle) "
            "VALUES (?, ?, ?, ?, ?)",
            (event, json.dumps(data, default=str), significance, time.time(), cycle),
        )
        self._pending_writes += 1
        self._maybe_commit()

    async def recall_episodic(self, event_pattern: str = "%",
                              limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM episodic WHERE event LIKE ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (event_pattern, limit),
        ).fetchall()
        return [
            {"event": r["event"], "data": json.loads(r["data"]),
             "significance": r["significance"], "timestamp": r["timestamp"],
             "cycle": r["cycle"]}
            for r in rows
        ]

    async def store_semantic(self, key: str, value: str,
                             tags: list[str] = None, confidence: float = 0.5):
        self._conn.execute(
            "INSERT OR REPLACE INTO semantic (key, value, tags, confidence, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, value, json.dumps(tags or []), confidence, time.time()),
        )
        self._pending_writes += 1
        self._maybe_commit()

    async def recall_semantic(self, key: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM semantic WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return {
                "key": row["key"], "value": row["value"],
                "tags": json.loads(row["tags"]),
                "confidence": row["confidence"],
            }
        return None

    async def search_semantic(self, tag: str = None,
                              query: str = None) -> list[dict]:
        if tag:
            rows = self._conn.execute(
                "SELECT * FROM semantic WHERE tags LIKE ?",
                (f'%"{tag}"%',),
            ).fetchall()
        elif query:
            rows = self._conn.execute(
                "SELECT * FROM semantic WHERE key LIKE ? OR value LIKE ?",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM semantic ORDER BY updated_at DESC LIMIT 50"
            ).fetchall()

        return [
            {"key": r["key"], "value": r["value"],
             "tags": json.loads(r["tags"]), "confidence": r["confidence"]}
            for r in rows
        ]

    async def store_procedural(self, strategy: str, context: str,
                               outcome: str, success: bool):
        self._conn.execute(
            "INSERT INTO procedural (strategy, context, outcome, success, learned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (strategy, context, outcome, int(success), time.time()),
        )
        self._pending_writes += 1
        self._maybe_commit()

    async def recall_strategies(self, context_pattern: str = "%",
                                only_successful: bool = False) -> list[dict]:
        query = "SELECT * FROM procedural WHERE context LIKE ?"
        if only_successful:
            query += " AND success = 1"
        query += " ORDER BY learned_at DESC LIMIT 20"

        rows = self._conn.execute(query, (context_pattern,)).fetchall()
        return [
            {"strategy": r["strategy"], "context": r["context"],
             "outcome": r["outcome"], "success": bool(r["success"])}
            for r in rows
        ]

    async def get_context_for_decision(self, topic: str) -> str:
        parts = []

        events = self._conn.execute(
            "SELECT * FROM episodic ORDER BY timestamp DESC LIMIT 15"
        ).fetchall()
        if events:
            parts.append("RECENT EVENTS:")
            for e in events[:6]:
                data = json.loads(e["data"])
                parts.append(
                    f"  - {e['event']} (cycle {e['cycle']}): "
                    f"{json.dumps(data, default=str)[:220]}"
                )

        related_events = self._conn.execute(
            "SELECT * FROM episodic WHERE event LIKE ? ORDER BY timestamp DESC LIMIT 8",
            (f"%{topic}%",),
        ).fetchall()
        if related_events:
            parts.append("\nRELATED EVENTS:")
            for e in related_events[:5]:
                data = json.loads(e["data"])
                parts.append(
                    f"  - {e['event']}: {json.dumps(data, default=str)[:220]}"
                )

        knowledge = await self.search_semantic(query=topic)
        if knowledge:
            parts.append("\nRELEVANT KNOWLEDGE:")
            for k in knowledge[:5]:
                parts.append(
                    f"  - {k['key']} (conf {k['confidence']:.0%}): {k['value'][:220]}"
                )

        strategies = await self.recall_strategies(
            context_pattern=f"%{topic}%", only_successful=True
        )
        if strategies:
            parts.append("\nPROVEN STRATEGIES:")
            for s in strategies[:3]:
                parts.append(f"  - {s['strategy']}: {s['outcome'][:200]}")

        failed = self._conn.execute(
            "SELECT * FROM procedural WHERE context LIKE ? AND success = 0 "
            "ORDER BY learned_at DESC LIMIT 5",
            (f"%{topic}%",),
        ).fetchall()
        if failed:
            parts.append("\nFAILURE PATTERNS TO AVOID:")
            for row in failed:
                parts.append(f"  - {row['strategy']}: {row['outcome'][:200]}")

        return "\n".join(parts) if parts else "No relevant memories found."

    def stats(self) -> dict:
        return {
            "episodic": self._conn.execute(
                "SELECT COUNT(*) FROM episodic"
            ).fetchone()[0],
            "semantic": self._conn.execute(
                "SELECT COUNT(*) FROM semantic"
            ).fetchone()[0],
            "procedural": self._conn.execute(
                "SELECT COUNT(*) FROM procedural"
            ).fetchone()[0],
        }

    def flush(self):
        self._maybe_commit(force=True)

    def close(self):
        if self._conn:
            self._maybe_commit(force=True)
            self._conn.close()
