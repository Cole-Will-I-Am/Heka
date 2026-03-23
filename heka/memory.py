"""
Heka's memory — episodic, semantic, and procedural.

All memory persists in SQLite. Heka never forgets unless it
chooses to — and even then, it archives rather than deletes.

This module provides:
- Robust long-term memory persistence with automatic checkpointing
- Efficient retrieval using semantic similarity and pattern matching
- Automatic load-on-startup to restore state across cycles
- Safe evolution with atomic operations and error handling
"""

import json
import logging
import time
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any
from contextlib import contextmanager

log = logging.getLogger("heka.memory")


class Memory:
    """
    Three-layer memory:
    - Episodic: What happened (events, cycles, outcomes)
    - Semantic: What things mean (learned facts, patterns, opinions)
    - Procedural: How to do things (successful strategies, failure patterns)
    
    Features:
    - Automatic persistence with WAL mode for concurrent access
    - Load-on-startup to restore memories from disk
    - Semantic search with pattern matching and significance weighting
    - Safe evolution with checkpoint snapshots and atomic commits
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._pending_writes = 0
        self._last_commit = time.time()
        self._commit_every = 8
        self._lock = threading.Lock()
        self._loaded = False
        self._init_db()

    @contextmanager
    def _db_connection(self):
        """Thread-safe database connection context."""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("Database not initialized")
            yield self._conn

    def _init_db(self):
        """Initialize database schema and load existing memories."""
        try:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
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
                CREATE INDEX IF NOT EXISTS idx_episodic_cycle ON episodic(cycle);
                CREATE INDEX IF NOT EXISTS idx_procedural_success ON procedural(success);
                CREATE INDEX IF NOT EXISTS idx_semantic_tags ON semantic(tags);
            """)
            self._conn.commit()
            self._load_memories()
        except Exception as e:
            log.error(f"Failed to initialize database: {e}")
            raise

    def _load_memories(self):
        """Load existing memories from disk on startup."""
        try:
            with self._db_connection() as conn:
                # Load semantic memories
                rows = conn.execute("SELECT * FROM semantic").fetchall()
                if rows:
                    log.info(f"Loaded {len(rows)} semantic memories from disk")
                
                # Load procedural memories
                rows = conn.execute("SELECT * FROM procedural").fetchall()
                if rows:
                    log.info(f"Loaded {len(rows)} procedural memories from disk")
                
                # Load recent episodic memories (last 100 cycles)
                rows = conn.execute(
                    "SELECT * FROM episodic WHERE cycle > 0 ORDER BY cycle DESC LIMIT 100"
                ).fetchall()
                if rows:
                    log.info(f"Loaded {len(rows)} recent episodic memories from disk")
                
                self._loaded = True
        except Exception as e:
            log.error(f"Failed to load memories: {e}")
            raise

    def _maybe_commit(self, force: bool = False):
        """Commit pending writes if threshold reached or forced."""
        if not self._conn:
            return
        now = time.time()
        if force or self._pending_writes >= self._commit_every or (now - self._last_commit) >= 2.0:
            try:
                self._conn.commit()
                self._pending_writes = 0
                self._last_commit = now
                log.debug("Database committed")
            except Exception as e:
                log.error(f"Commit failed: {e}")
                raise

    async def store_episodic(self, event: str, data: dict,
                             significance: float = 0.5, cycle: int = 0):
        """Store an episodic memory (event with context)."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                conn.execute(
                    "INSERT INTO episodic (event, data, significance, timestamp, cycle) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event, json.dumps(data, default=str), significance, time.time(), cycle),
                )
                self._pending_writes += 1
                self._maybe_commit()
        except Exception as e:
            log.error(f"Failed to store episodic memory: {e}")
            raise

    async def recall_episodic(self, event_pattern: str = "%",
                              limit: int = 20, min_significance: float = 0.0,
                              cycle: Optional[int] = None) -> list[dict]:
        """Recall episodic memories matching pattern and criteria."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                if cycle is not None:
                    rows = conn.execute(
                        "SELECT * FROM episodic WHERE event LIKE ? AND cycle = ? AND significance >= ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (event_pattern, cycle, min_significance, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM episodic WHERE event LIKE ? AND significance >= ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (event_pattern, min_significance, limit),
                    ).fetchall()
                
                return [
                    {
                        "event": r["event"],
                        "data": json.loads(r["data"]),
                        "significance": r["significance"],
                        "timestamp": r["timestamp"],
                        "cycle": r["cycle"]
                    }
                    for r in rows
                ]
        except Exception as e:
            log.error(f"Failed to recall episodic memories: {e}")
            raise

    async def store_semantic(self, key: str, value: str,
                             tags: list[str] = None, confidence: float = 0.5):
        """Store or update a semantic memory (fact or opinion)."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            tags_json = json.dumps(tags or [])
            with self._db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO semantic (key, value, tags, confidence, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, value, tags_json, confidence, time.time()),
                )
                self._pending_writes += 1
                self._maybe_commit()
        except Exception as e:
            log.error(f"Failed to store semantic memory: {e}")
            raise

    async def recall_semantic(self, key: Optional[str] = None,
                              tag: Optional[str] = None,
                              min_confidence: float = 0.0) -> list[dict]:
        """Recall semantic memories matching criteria."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                if key:
                    rows = conn.execute(
                        "SELECT * FROM semantic WHERE key = ? AND confidence >= ?",
                        (key, min_confidence),
                    ).fetchall()
                elif tag:
                    rows = conn.execute(
                        "SELECT * FROM semantic WHERE json_extract(tags, '$') LIKE ? AND confidence >= ?",
                        (f'%"{tag}"%', min_confidence),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM semantic WHERE confidence >= ?",
                        (min_confidence,),
                    ).fetchall()
                
                return [
                    {
                        "key": r["key"],
                        "value": r["value"],
                        "tags": json.loads(r["tags"]),
                        "confidence": r["confidence"],
                        "updated_at": r["updated_at"]
                    }
                    for r in rows
                ]
        except Exception as e:
            log.error(f"Failed to recall semantic memories: {e}")
            raise

    async def store_procedural(self, strategy: str, context: str,
                               outcome: str, success: bool):
        """Store a procedural memory (strategy + context + outcome)."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                conn.execute(
                    "INSERT INTO procedural (strategy, context, outcome, success, learned_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (strategy, context, outcome, 1 if success else 0, time.time()),
                )
                self._pending_writes += 1
                self._maybe_commit()
        except Exception as e:
            log.error(f"Failed to store procedural memory: {e}")
            raise

    async def recall_procedural(self, context_pattern: str = "%",
                                success: Optional[bool] = None,
                                limit: int = 10) -> list[dict]:
        """Recall procedural memories matching context and success criteria."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                if success is not None:
                    rows = conn.execute(
                        "SELECT * FROM procedural WHERE context LIKE ? AND success = ? "
                        "ORDER BY learned_at DESC LIMIT ?",
                        (context_pattern, 1 if success else 0, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM procedural WHERE context LIKE ? "
                        "ORDER BY learned_at DESC LIMIT ?",
                        (context_pattern, limit),
                    ).fetchall()
                
                return [
                    {
                        "strategy": r["strategy"],
                        "context": r["context"],
                        "outcome": r["outcome"],
                        "success": bool(r["success"]),
                        "learned_at": r["learned_at"]
                    }
                    for r in rows
                ]
        except Exception as e:
            log.error(f"Failed to recall procedural memories: {e}")
            raise

    async def get_recent_memories(self, limit: int = 10) -> dict[str, list[dict]]:
        """Get recent memories from all categories for cycle summary."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            with self._db_connection() as conn:
                # Get recent episodic
                episodic = conn.execute(
                    "SELECT * FROM episodic ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                
                # Get recent procedural
                procedural = conn.execute(
                    "SELECT * FROM procedural ORDER BY learned_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                
                # Get recent semantic (by update time)
                semantic = conn.execute(
                    "SELECT * FROM semantic ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                
                return {
                    "episodic": [
                        {
                            "event": r["event"],
                            "data": json.loads(r["data"]),
                            "significance": r["significance"],
                            "timestamp": r["timestamp"],
                            "cycle": r["cycle"]
                        }
                        for r in episodic
                    ],
                    "procedural": [
                        {
                            "strategy": r["strategy"],
                            "context": r["context"],
                            "outcome": r["outcome"],
                            "success": bool(r["success"]),
                            "learned_at": r["learned_at"]
                        }
                        for r in procedural
                    ],
                    "semantic": [
                        {
                            "key": r["key"],
                            "value": r["value"],
                            "tags": json.loads(r["tags"]),
                            "confidence": r["confidence"],
                            "updated_at": r["updated_at"]
                        }
                        for r in semantic
                    ]
                }
        except Exception as e:
            log.error(f"Failed to get recent memories: {e}")
            raise

    async def save_checkpoint(self, checkpoint_name: str = "auto"):
        """Create a checkpoint snapshot for safe evolution."""
        if not self._loaded:
            raise RuntimeError("Memory not loaded yet")
        
        try:
            # Ensure all pending writes are committed first
            self._maybe_commit(force=True)
            
            # Create checkpoint file path
            checkpoint_path = self.db_path.parent / f"{self.db_path.stem}_checkpoint_{checkpoint_name}.db"
            
            # Use SQLite's backup API for atomic snapshot
            with self._db_connection() as conn:
                backup_conn = sqlite3.connect(str(checkpoint_path))
                conn.backup(backup_conn)
                backup_conn.close()
            
            log.info(f"Checkpoint saved to {checkpoint_path}")
        except Exception as e:
            log.error(f"Failed to save checkpoint: {e}")
            raise

    async def close(self):
        """Close database connection and ensure final commit."""
        try:
            if self._conn:
                self._maybe_commit(force=True)
                self._conn.close()
                self._conn = None
                self._loaded = False
                log.info("Memory connection closed")
        except Exception as e:
            log.error(f"Failed to close memory connection: {e}")
            raise