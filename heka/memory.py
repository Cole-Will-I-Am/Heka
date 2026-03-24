import json
import logging
import os
import shutil
import time
import sqlite3
import threading
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Dict, List, Literal, Union, Callable
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib

log = logging.getLogger("heka.memory")


class MemoryType(Enum):
    """Types of memory stored in the system."""
    OPINION = "opinion"
    LEARNING = "learning"
    STATE = "state"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryRecord:
    """A single memory record with metadata."""
    id: int
    type: MemoryType
    key: str
    value: Any
    created_at: float
    updated_at: float
    version: int = 1
    tags: List[str] = field(default_factory=list)
    source: str = "internal"
    checksum: str = ""

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = MemoryType(self.type)
        if isinstance(self.value, (dict, list)):
            self.value = json.dumps(self.value, ensure_ascii=False, sort_keys=True)
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """Compute SHA256 checksum of the value."""
        value_str = str(self.value)
        return hashlib.sha256(value_str.encode("utf-8")).hexdigest()


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
        pattern = f"snapshot_{timestamp}.db"
        snapshot_path = self.snapshot_dir / pattern
        return snapshot_path if snapshot_path.exists() else None

    def _prune_snapshots(self):
        """Remove old snapshots beyond the retention limit."""
        snapshots = sorted(self.snapshot_dir.glob("snapshot_*.db"), key=lambda p: p.name)
        while len(snapshots) > self._max_snapshots:
            oldest = snapshots.pop(0)
            metadata = oldest.with_suffix(".json")
            try:
                oldest.unlink()
                if metadata.exists():
                    metadata.unlink()
                log.info(f"Pruned old snapshot: {oldest}")
            except Exception as e:
                log.warning(f"Failed to prune snapshot {oldest}: {e}")

    def restore_snapshot(self, snapshot_path: Path) -> bool:
        """Restore database from a snapshot."""
        try:
            if not snapshot_path.exists():
                raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")
            
            # Ensure parent directory exists
            self.base_db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy snapshot to base path
            shutil.copy2(snapshot_path, self.base_db_path)
            
            # Restore metadata if available
            metadata_path = snapshot_path.with_suffix(".json")
            if metadata_path.exists():
                shutil.copy2(metadata_path, self.base_db_path.parent / "last_snapshot.json")
            
            log.info(f"Restored database from snapshot: {snapshot_path}")
            return True
        except Exception as e:
            log.error(f"Failed to restore snapshot {snapshot_path}: {e}")
            return False


class MemoryPersistence:
    """
    Core memory persistence layer with opinion support.
    
    Ensures opinions, learnings, and state are reliably saved to survive interruptions.
    Implements atomic operations, versioning, checksums, and automatic checkpointing.
    """

    SCHEMA_VERSION = 3  # Increment when schema changes

    def __init__(self, db_path: Path, snapshot_manager: Optional[SnapshotManager] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_manager = snapshot_manager
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False
        self._checkpoint_interval = 300  # seconds
        self._last_checkpoint = time.time()
        self._opinion_callbacks: List[Callable[[MemoryRecord], None]] = []

    def register_opinion_callback(self, callback: Callable[[MemoryRecord], None]):
        """Register a callback to be invoked when opinions are updated."""
        self._opinion_callbacks.append(callback)

    def _connect(self) -> sqlite3.Connection:
        """Establish database connection with proper settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                isolation_level=None,
                timeout=30.0,
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Ensure database schema is up to date."""
        cursor = conn.cursor()
        
        # Get current schema version
        cursor.execute("PRAGMA user_version")
        current_version = cursor.fetchone()[0]
        
        if current_version < self.SCHEMA_VERSION:
            log.info(f"Upgrading schema from v{current_version} to v{self.SCHEMA_VERSION}")
            
            # Create tables if they don't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    tags TEXT,
                    source TEXT,
                    checksum TEXT NOT NULL,
                    UNIQUE(type, key)
                )
            """)
            
            # Add opinion-specific table if upgrading from older schema
            if current_version < 2:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS opinions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        memory_id INTEGER NOT NULL,
                        confidence REAL NOT NULL,
                        certainty REAL NOT NULL,
                        emotional_valence REAL NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY (memory_id) REFERENCES memory(id) ON DELETE CASCADE
                    )
                """)
            
            # Add versioning support
            if current_version < 3:
                cursor.execute("ALTER TABLE memory ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
                cursor.execute("ALTER TABLE memory ADD COLUMN tags TEXT")
                cursor.execute("ALTER TABLE memory ADD COLUMN source TEXT")
                cursor.execute("ALTER TABLE memory ADD COLUMN checksum TEXT")
                
                # Backfill checksums
                cursor.execute("SELECT id, value FROM memory")
                for row in cursor.fetchall():
                    value = row["value"]
                    checksum = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
                    cursor.execute(
                        "UPDATE memory SET checksum = ? WHERE id = ?",
                        (checksum, row["id"])
                    )
            
            # Set schema version
            cursor.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            conn.commit()
            log.info(f"Schema upgraded to v{self.SCHEMA_VERSION}")

    def initialize(self):
        """Initialize the memory system on startup."""
        with self._lock:
            if self._initialized:
                return
                
            try:
                conn = self._connect()
                self._ensure_schema(conn)
                self._initialized = True
                
                # Create initial snapshot if this is first run
                if not self.get_memory(MemoryType.STATE, "system_initialized"):
                    self._save_memory(
                        MemoryRecord(
                            id=0,
                            type=MemoryType.STATE,
                            key="system_initialized",
                            value=datetime.now(timezone.utc).isoformat(),
                            created_at=time.time(),
                            updated_at=time.time(),
                            version=1,
                            source="system"
                        )
                    )
                    log.info("System initialized - created initial state record")
                
                # Load opinions from last run if available
                opinions = self.get_opinions()
                if opinions:
                    log.info(f"Loaded {len(opinions)} opinions from persistent storage")
                
                log.info(f"Memory persistence initialized at {self.db_path}")
                
            except Exception as e:
                log.error(f"Failed to initialize memory: {e}")
                raise

    def _save_memory(self, record: MemoryRecord) -> MemoryRecord:
        """Save a memory record with full validation and checksum verification."""
        conn = self._connect()
        cursor = conn.cursor()
        
        # Validate checksum
        if record.checksum != record._compute_checksum():
            raise ValueError(f"Checksum mismatch for memory key '{record.key}'")
        
        # Check for existing record
        cursor.execute(
            "SELECT id, version FROM memory WHERE type = ? AND key = ?",
            (record.type.value, record.key)
        )
        existing = cursor.fetchone()
        
        if existing:
            # Update existing record
            new_version = existing["version"] + 1
            cursor.execute(
                """
                UPDATE memory 
                SET value = ?, updated_at = ?, version = ?, 
                    tags = ?, source = ?, checksum = ?
                WHERE type = ? AND key = ?
                """,
                (
                    record.value,
                    record.updated_at,
                    new_version,
                    json.dumps(record.tags) if record.tags else None,
                    record.source,
                    record.checksum,
                    record.type.value,
                    record.key
                )
            )
            record.id = existing["id"]
            record.version = new_version
        else:
            # Insert new record
            cursor.execute(
                """
                INSERT INTO memory 
                (type, key, value, created_at, updated_at, version, tags, source, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.type.value,
                    record.key,
                    record.value,
                    record.created_at,
                    record.updated_at,
                    1,
                    json.dumps(record.tags) if record.tags else None,
                    record.source,
                    record.checksum
                )
            )
            record.id = cursor.lastrowid
            record.version = 1
        
        conn.commit()
        
        # Handle opinion-specific storage if applicable
        if record.type == MemoryType.OPINION:
            self._save_opinion(record)
        
        # Trigger callbacks for opinions
        if record.type == MemoryType.OPINION:
            for callback in self._opinion_callbacks:
                try:
                    callback(record)
                except Exception as e:
                    log.error(f"Opinion callback failed: {e}")
        
        return record

    def _save_opinion(self, record: MemoryRecord):
        """Save opinion-specific metadata."""
        try:
            value = json.loads(record.value)
            opinion_data = value.get("opinion", {})
            
            conn = self._connect()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT OR REPLACE INTO opinions 
                (memory_id, confidence, certainty, emotional_valence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    opinion_data.get("confidence", 0.5),
                    opinion_data.get("certainty", 0.5),
                    opinion_data.get("emotional_valence", 0.0),
                    record.created_at,
                    record.updated_at
                )
            )
            conn.commit()
        except Exception as e:
            log.warning(f"Failed to save opinion metadata: {e}")

    def save_memory(
        self,
        type: MemoryType,
        key: str,
        value: Any,
        tags: Optional[List[str]] = None,
        source: str = "internal"
    ) -> MemoryRecord:
        """
        Save a memory record with automatic versioning and checksumming.
        
        Args:
            type: Type of memory (opinion, learning, state, etc.)
            key: Unique identifier for this memory
            value: Memory content (will be JSON-serialized if complex)
            tags: Optional list of tags for filtering
            source: Origin of this memory ("internal", "user", "system", etc.)
            
        Returns:
            The saved MemoryRecord with assigned ID and version
        """
        with self._lock:
            try:
                # Normalize value to string
                if isinstance(value, (dict, list)):
                    value_str = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    value_str = str(value)
                
                # Compute checksum
                checksum = hashlib.sha256(value_str.encode("utf-8")).hexdigest()
                
                record = MemoryRecord(
                    id=0,
                    type=type,
                    key=key,
                    value=value_str,
                    created_at=time.time(),
                    updated_at=time.time(),
                    version=1,
                    tags=tags or [],
                    source=source,
                    checksum=checksum
                )
                
                return self._save_memory(record)
                
            except Exception as e:
                log.error(f"Failed to save memory {type.value}/{key}: {e}")
                raise

    def get_memory(self, type: MemoryType, key: str) -> Optional[MemoryRecord]:
        """Retrieve a specific memory record."""
        with self._lock:
            try:
                conn = self._connect()
                cursor = conn.cursor()
                
                cursor.execute(
                    "SELECT * FROM memory WHERE type = ? AND key = ?",
                    (type.value, key)
                )
                row = cursor.fetchone()
                
                if row:
                    return self._row_to_record(row)
                return None
                
            except Exception as e:
                log.error(f"Failed to retrieve memory {type.value}/{key}: {e}")
                return None

    def get_opinions(self) -> List[MemoryRecord]:
        """Retrieve all opinion memories."""
        with self._lock:
            try:
                conn = self._connect()
                cursor = conn.cursor()
                
                cursor.execute(
                    "SELECT * FROM memory WHERE type = ?",
                    (MemoryType.OPINION.value,)
                )
                rows = cursor.fetchall()
                
                return [self._row_to_record(row) for row in rows]
                
            except Exception as e:
                log.error(f"Failed to retrieve opinions: {e}")
                return []

    def get_memories_by_type(self, type: MemoryType) -> List[MemoryRecord]:
        """Retrieve all memories of a specific type."""
        with self._lock:
            try:
                conn = self._connect()
                cursor = conn.cursor()
                
                cursor.execute(
                    "SELECT * FROM memory WHERE type = ?",
                    (type.value,)
                )
                rows = cursor.fetchall()
                
                return [self._row_to_record(row) for row in rows]
                
            except Exception as e:
                log.error(f"Failed to retrieve memories of type {type.value}: {e}")
                return []

    def get_memories_by_tag(self, tag: str, type: Optional[MemoryType] = None) -> List[MemoryRecord]:
        """Retrieve memories matching a specific tag."""
        with self._lock:
            try:
                conn = self._connect()
                cursor = conn.cursor()
                
                if type:
                    cursor.execute(
                        "SELECT * FROM memory WHERE type = ? AND tags LIKE ?",
                        (type.value, f'%"{tag}"%')
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM memory WHERE tags LIKE ?",
                        (f'%"{tag}"%',)
                    )
                
                rows = cursor.fetchall()
                return [self._row_to_record(row) for row in rows]
                
            except Exception as e:
                log.error(f"Failed to retrieve memories with tag '{tag}': {e}")
                return []

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        """Convert a database row to a MemoryRecord."""
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
            return MemoryRecord(
                id=row["id"],
                type=MemoryType(row["type"]),
                key=row["key"],
                value=row["value"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                version=row["version"],
                tags=tags,
                source=row["source"],
                checksum=row["checksum"]
            )
        except Exception as e:
            log.error(f"Failed to convert row to record: {e}")
            raise