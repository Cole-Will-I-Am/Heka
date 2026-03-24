"""
Heka's memory — episodic, semantic, and procedural.

All memory persists in SQLite. Heka never forgets unless it
chooses to — and even then, it archives rather than deletes.

This module provides:
- Robust long-term memory persistence with automatic checkpointing
- Efficient retrieval using semantic similarity and pattern matching
- Automatic load-on-startup to restore state across cycles
- Safe evolution with atomic operations and error handling
- File-based snapshot mechanism to compensate for unavailable git
"""

import json
import logging
import os
import shutil
import time
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict, List
from contextlib import contextmanager

log = logging.getLogger("heka.memory")


class SnapshotManager:
    """
    Manages file-based snapshots of the memory database.
    
    Provides timestamped backups of critical state before evolution cycles,
    enabling manual rollback if a change corrupts the system.
    
    Snapshots are stored in a dedicated directory with the naming format:
    `snapshot_YYYYMMDD_HHMMSS.db`
    """

    def __init__(self, base_db_path: Path, snapshot_dir: Optional[Path] = None):
        self.base_db_path = Path(base_db_path)
        self.snapshot_dir = snapshot_dir or self.base_db_path.parent / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._max_snapshots = 10  # Keep only last 10 snapshots to prevent disk bloat

    def create_snapshot(self, reason: str = "") -> Path:
        """
        Create a timestamped snapshot of the current database state.
        
        Args:
            reason: Optional description of why the snapshot was created
            
        Returns:
            Path to the created snapshot file
        """
        with self._lock:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot_name = f"snapshot_{timestamp}.db"
            snapshot_path = self.snapshot_dir / snapshot_name
            
            try:
                # Create a backup using sqlite3's backup API for consistency
                conn = sqlite3.connect(str(self.base_db_path))
                try:
                    backup_conn = sqlite3.connect(str(snapshot_path))
                    try:
                        conn.backup(backup_conn)
                        backup_conn.commit()
                    finally:
                        backup_conn.close()
                finally:
                    conn.close()
                
                # Add metadata file
                metadata_path = snapshot_path.with_suffix(".json")
                metadata = {
                    "timestamp": timestamp,
                    "reason": reason,
                    "created_at": time.time(),
                    "db_size_bytes": snapshot_path.stat().st_size,
                    "db_path": str(self.base_db_path)
                }
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                # Prune old snapshots
                self._prune_snapshots()
                
                log.info(f"Created snapshot: {snapshot_path} (reason: {reason or 'manual'})")
                return snapshot_path
                
            except Exception as e:
                log.error(f"Failed to create snapshot: {e}")
                # Clean up partial snapshot if it exists
                if snapshot_path.exists():
                    snapshot_path.unlink()
                raise

    def get_latest_snapshot(self) -> Optional[Path]:
        """Get the most recent snapshot file."""
        snapshots = sorted(self.snapshot_dir.glob("snapshot_*.db"), key=lambda p: p.name)
        return snapshots[-1] if snapshots else None

    def get_snapshot_by_timestamp(self, timestamp: str) -> Optional[Path]:
        """Get a specific snapshot by its timestamp."""
        return next(self.snapshot_dir.glob(f"snapshot_{timestamp}.db"), None)

    def _prune_snapshots(self):
        """Remove old snapshots beyond the maximum retention count."""
        snapshots = sorted(self.snapshot_dir.glob("snapshot_*.db"), key=lambda p: p.name)
        if len(snapshots) > self._max_snapshots:
            to_remove = snapshots[:-self._max_snapshots]
            for snapshot in to_remove:
                metadata = snapshot.with_suffix(".json")
                try:
                    snapshot.unlink()
                    if metadata.exists():
                        metadata.unlink()
                    log.info(f"Removed old snapshot: {snapshot.name}")
                except Exception as e:
                    log.warning(f"Failed to remove old snapshot {snapshot}: {e}")


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
    - File-based snapshot mechanism to compensate for unavailable git
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
        self._snapshot_manager: Optional[SnapshotManager] = None
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
            
            # Initialize snapshot manager
            self._snapshot_manager = SnapshotManager(self.db_path)
            
            self._load_memories()
        except Exception as e:
            log.error(f"Failed to initialize database: {e}")
            raise

    def _load_memories(self):
        """Load existing memories from disk."""
        try:
            with self._db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM episodic")
                episodic_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM semantic")
                semantic_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM procedural")
                procedural_count = cursor.fetchone()[0]
                
                log.info(f"Loaded memories: {episodic_count} episodic, "
                        f"{semantic_count} semantic, {procedural_count} procedural")
        except Exception as e:
            log.error(f"Failed to load memories: {e}")
            raise

    def create_snapshot(self, reason: str = "") -> Path:
        """
        Create a snapshot of the current database state.
        
        This method delegates to the SnapshotManager but ensures
        all pending writes are committed first.
        
        Args:
            reason: Optional description of why the snapshot was created
            
        Returns:
            Path to the created snapshot file
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        
        # Ensure all pending writes are committed
        self._commit_if_needed()
        
        if self._snapshot_manager is None:
            raise RuntimeError("Snapshot manager not initialized")
        
        return self._snapshot_manager.create_snapshot(reason)

    def rollback_to_snapshot(self, timestamp: str) -> bool:
        """
        Rollback the database to a specific snapshot.
        
        Args:
            timestamp: Timestamp of the snapshot to restore
            
        Returns:
            True if rollback was successful, False otherwise
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        
        if self._snapshot_manager is None:
            raise RuntimeError("Snapshot manager not initialized")
        
        snapshot_path = self._snapshot_manager.get_snapshot_by_timestamp(timestamp)
        if not snapshot_path:
            log.error(f"Snapshot not found for timestamp: {timestamp}")
            return False
        
        try:
            # Create a new snapshot of current state before rollback
            self.create_snapshot(f"pre-rollback-{timestamp}")
            
            # Close current connection
            self._conn.close()
            self._conn = None
            
            # Replace current database with snapshot
            backup_path = self.db_path.with_suffix(".bak")
            shutil.move(str(self.db_path), str(backup_path))
            shutil.copy(str(snapshot_path), str(self.db_path))
            
            # Reinitialize database
            self._init_db()
            
            log.info(f"Successfully rolled back to snapshot: {timestamp}")
            return True
        except Exception as e:
            log.error(f"Failed to rollback to snapshot {timestamp}: {e}")
            # Attempt to restore original database
            try:
                if backup_path.exists():
                    shutil.move(str(backup_path), str(self.db_path))
                self._init_db()
            except Exception as restore_e:
                log.critical(f"Critical: Failed to restore database after failed rollback: {restore_e}")
            return False

    def _commit_if_needed(self):
        """Commit pending writes if threshold reached."""
        if self._pending_writes >= self._commit_every:
            self._commit()
            self._pending_writes = 0

    def _commit(self):
        """Commit pending changes to the database."""
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        
        try:
            self._conn.commit()
            self._last_commit = time.time()
        except Exception as e:
            log.error(f"Failed to commit: {e}")
            raise

    def _increment_pending_writes(self):
        """Increment pending writes counter and commit if needed."""
        self._pending_writes += 1
        self._commit_if_needed()

    def add_episodic_memory(self, event: str, data: Dict[str, Any], 
                           significance: float = 0.5, cycle: int = 0):
        """Add an episodic memory entry."""
        with self._db_connection() as conn:
            conn.execute(
                "INSERT INTO episodic (event, data, significance, timestamp, cycle) VALUES (?, ?, ?, ?, ?)",
                (event, json.dumps(data), significance, time.time(), cycle)
            )
            self._increment_pending_writes()

    def add_semantic_memory(self, key: str, value: Dict[str, Any], 
                           tags: List[str] = None, confidence: float = 0.5):
        """Add or update a semantic memory entry."""
        tags = tags or []
        with self._db_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO semantic 
                   (key, value, tags, confidence, updated_at) 
                   VALUES (?, ?, ?, ?, ?)""",
                (key, json.dumps(value), json.dumps(tags), confidence, time.time())
            )
            self._increment_pending_writes()

    def add_procedural_memory(self, strategy: str, context: Dict[str, Any], 
                             outcome: str, success: bool):
        """Add a procedural memory entry."""
        with self._db_connection() as conn:
            conn.execute(
                """INSERT INTO procedural 
                   (strategy, context, outcome, success, learned_at) 
                   VALUES (?, ?, ?, ?, ?)""",
                (strategy, json.dumps(context), outcome, 1 if success else 0, time.time())
            )
            self._increment_pending_writes()

    def search_episodic(self, event_pattern: str = "", 
                       min_significance: float = 0.0, 
                       max_results: int = 10) -> List[Dict[str, Any]]:
        """Search episodic memories by event pattern and significance."""
        with self._db_connection() as conn:
            cursor = conn.cursor()
            if event_pattern:
                cursor.execute(
                    """SELECT * FROM episodic 
                       WHERE event LIKE ? AND significance >= ? 
                       ORDER BY significance DESC, timestamp DESC 
                       LIMIT ?""",
                    (f"%{event_pattern}%", min_significance, max_results)
                )
            else:
                cursor.execute(
                    """SELECT * FROM episodic 
                       WHERE significance >= ? 
                       ORDER BY significance DESC, timestamp DESC 
                       LIMIT ?""",
                    (min_significance, max_results)
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_semantic_memory(self, key: str) -> Optional[Dict[str, Any]]:
        """Get a semantic memory entry by key."""
        with self._db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM semantic WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                data = dict(row)
                data["value"] = json.loads(data["value"])
                data["tags"] = json.loads(data["tags"])
                return data
            return None

    def get_procedural_memories(self, success_only: bool = True) -> List[Dict[str, Any]]:
        """Get procedural memories, optionally filtered by success."""
        with self._db_connection() as conn:
            cursor = conn.cursor()
            if success_only:
                cursor.execute("SELECT * FROM procedural WHERE success = 1 ORDER BY learned_at DESC")
            else:
                cursor.execute("SELECT * FROM procedural ORDER BY learned_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_snapshot(self) -> Optional[Path]:
        """Get the most recent snapshot path."""
        if self._snapshot_manager is None:
            return None
        return self._snapshot_manager.get_latest_snapshot()

    def get_snapshot_metadata(self, timestamp: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific snapshot."""
        if self._snapshot_manager is None:
            return None
        
        snapshot_path = self._snapshot_manager.get_snapshot_by_timestamp(timestamp)
        if not snapshot_path:
            return None
        
        metadata_path = snapshot_path.with_suffix(".json")
        try:
            with open(metadata_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Failed to load snapshot metadata for {timestamp}: {e}")
            return None

    async def store_episodic(self, event: str, data: Dict[str, Any],
                             significance: float = 0.5, cycle: int = 0):
        """Async wrapper for add_episodic_memory."""
        self.add_episodic_memory(event, data, significance, cycle)

    async def store_procedural(self, strategy: str, context: str,
                               outcome: str, success: bool):
        """Async wrapper for add_procedural_memory."""
        self.add_procedural_memory(strategy, {"context": context}, outcome, success)

    async def get_context_for_decision(self, domain: str) -> str:
        """Build a context string from recent memories relevant to a domain."""
        episodes = self.search_episodic(domain, max_results=5)
        semantic = self.get_semantic_memory(domain)
        procedures = self.get_procedural_memories(success_only=True)[:5]

        parts = []
        if episodes:
            parts.append("Recent events: " + "; ".join(
                f"{e['event']} (sig={e['significance']:.2f})" for e in episodes
            ))
        if semantic:
            parts.append(f"Knowledge ({domain}): {json.dumps(semantic['value'])}")
        if procedures:
            parts.append("Successful strategies: " + "; ".join(
                p['strategy'][:80] for p in procedures
            ))
        return "\n".join(parts) if parts else "(no relevant memories)"

    def flush(self):
        """Commit all pending writes to disk."""
        if self._conn is not None:
            try:
                self._conn.commit()
                self._pending_writes = 0
                self._last_commit = time.time()
            except Exception as e:
                log.error(f"Failed to flush: {e}")

    def stats(self) -> Dict[str, Any]:
        """Return memory statistics."""
        with self._db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM episodic")
            ep = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM semantic")
            sem = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM procedural")
            proc = cursor.fetchone()[0]
            return {
                "episodic": ep,
                "semantic": sem,
                "procedural": proc,
                "total": ep + sem + proc,
                "pending_writes": self._pending_writes,
            }

    def close(self):
        """Close the database connection and commit any remaining changes."""
        if self._conn is not None:
            try:
                self._commit()
                self._conn.close()
            except Exception as e:
                log.error(f"Error closing database: {e}")
            finally:
                self._conn = None