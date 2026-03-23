"""
Heka's journal — structured event logging with checkpoint capability.

Every significant event is recorded. The journal is both a log
and a narrative — Heka writes about itself in first person.

Checkpoint system enables recoverable snapshots for evolution cycles,
providing rollback ability in the absence of git and ensuring memory
persistence for long-term learning.
"""

import json
import logging
import time
import shutil
import hashlib
import asyncio
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

log = logging.getLogger("heka.journal")


@dataclass
class Entry:
    timestamp: float
    cycle: int
    event: str
    category: str  # "evolution", "thought", "decision", "error", "milestone"
    data: dict
    narrative: Optional[str] = None


@dataclass
class Checkpoint:
    """Represents a recoverable snapshot of system state."""
    timestamp: float
    cycle: int
    checkpoint_id: str
    journal_hash: str
    state_hash: str
    metadata: dict
    path: Path


class Journal:
    """Appends structured entries to JSONL + human-readable narrative."""

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.journal_path = self.base_path / ".heka" / "journal.jsonl"
        self.narrative_path = self.base_path / ".heka" / "narrative.log"
        self.checkpoints_dir = self.base_path / ".heka" / "checkpoints"
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def record(self, cycle: int, event: str, category: str, data: dict,
               narrative: str = None):
        entry = Entry(
            timestamp=time.time(),
            cycle=cycle,
            event=event,
            category=category,
            data=data,
            narrative=narrative,
        )

        with open(self.journal_path, "a") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")

        if narrative:
            with open(self.narrative_path, "a") as f:
                f.write(f"[Cycle {cycle}] {narrative}\n")

        log.info(f"[{category}] {event}: {narrative or json.dumps(data)[:100]}")

    def read_recent(self, limit: int = 20) -> list[dict]:
        entries = []
        if not self.journal_path.exists():
            return entries

        with open(self.journal_path) as f:
            lines = f.readlines()

        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue

        return entries

    def read_by_category(self, category: str, limit: int = 20) -> list[dict]:
        entries = []
        if not self.journal_path.exists():
            return entries

        with open(self.journal_path) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("category") == category:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries[-limit:]

    def cycle_summary(self, cycle: int) -> dict:
        entries = []
        if not self.journal_path.exists():
            return {"cycle": cycle, "entries": entries}

        with open(self.journal_path) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("cycle") == cycle:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return {"cycle": cycle, "entry_count": len(entries), "entries": entries}

    def stats(self) -> dict:
        if not self.journal_path.exists():
            return {"total_entries": 0, "categories": {}}

        total = 0
        categories: dict[str, int] = {}
        with open(self.journal_path) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    total += 1
                    cat = entry.get("category", "unknown")
                    categories[cat] = categories.get(cat, 0) + 1
                except json.JSONDecodeError:
                    continue

        return {"total_entries": total, "categories": categories}

    def _compute_file_hash(self, path: Path) -> str:
        """Compute SHA256 hash of a file."""
        if not path.exists():
            return ""
        sha256 = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (IOError, OSError) as e:
            log.error(f"Failed to compute hash for {path}: {e}")
            return ""

    def _compute_journal_hash(self) -> str:
        """Compute hash of the entire journal state."""
        return self._compute_file_hash(self.journal_path)

    async def create_checkpoint(self, cycle: int, state_data: dict | None = None) -> Checkpoint:
        """Create a recoverable snapshot of the current journal state."""
        checkpoint_id = f"cycle_{cycle}_{int(time.time())}"
        checkpoint_dir = self.checkpoints_dir / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Copy journal and narrative files
        journal_backup = checkpoint_dir / "journal.jsonl"
        narrative_backup = checkpoint_dir / "narrative.log"
        
        try:
            if self.journal_path.exists():
                shutil.copy2(self.journal_path, journal_backup)
            if self.narrative_path.exists():
                shutil.copy2(self.narrative_path, narrative_backup)
        except (shutil.Error, OSError) as e:
            log.error(f"Failed to backup journal files for checkpoint {checkpoint_id}: {e}")
            # Clean up partial checkpoint
            shutil.rmtree(checkpoint_dir, ignore_errors=True)
            raise

        # Compute hashes
        journal_hash = self._compute_file_hash(journal_backup)
        state_hash = hashlib.sha256(
            json.dumps(state_data or {}, sort_keys=True).encode()
        ).hexdigest() if state_data else ""

        # Create checkpoint metadata
        checkpoint = Checkpoint(
            timestamp=time.time(),
            cycle=cycle,
            checkpoint_id=checkpoint_id,
            journal_hash=journal_hash,
            state_hash=state_hash,
            metadata={
                "created_by": "heka.journal",
                "journal_lines": len(open(self.journal_path).readlines()) if self.journal_path.exists() else 0,
            },
            path=checkpoint_dir,
        )

        # Save checkpoint metadata
        metadata_path = checkpoint_dir / "checkpoint.json"
        try:
            with open(metadata_path, "w") as f:
                json.dump(asdict(checkpoint), f, indent=2)
        except (IOError, OSError) as e:
            log.error(f"Failed to save checkpoint metadata for {checkpoint_id}: {e}")
            shutil.rmtree(checkpoint_dir, ignore_errors=True)
            raise

        log.info(f"Created checkpoint {checkpoint_id} for cycle {cycle}")
        return checkpoint

    async def list_checkpoints(self) -> list[Checkpoint]:
        """List all available checkpoints."""
        checkpoints = []
        
        if not self.checkpoints_dir.exists():
            return checkpoints

        for checkpoint_dir in sorted(self.checkpoints_dir.iterdir()):
            if checkpoint_dir.is_dir():
                metadata_path = checkpoint_dir / "checkpoint.json"
                if metadata_path.exists():
                    try:
                        with open(metadata_path) as f:
                            data = json.load(f)
                        checkpoints.append(Checkpoint(**data))
                    except (json.JSONDecodeError, TypeError) as e:
                        log.warning(f"Invalid checkpoint metadata at {metadata_path}: {e}")
                        continue

        return checkpoints

    async def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get a specific checkpoint by ID."""
        checkpoint_dir = self.checkpoints_dir / checkpoint_id
        metadata_path = checkpoint_dir / "checkpoint.json"
        
        if not metadata_path.exists():
            return None

        try:
            with open(metadata_path) as f:
                data = json.load(f)
            return Checkpoint(**data)
        except (json.JSONDecodeError, TypeError) as e:
            log.error(f"Failed to load checkpoint {checkpoint_id}: {e}")
            return None

    @asynccontextmanager
    async def restore_checkpoint(self, checkpoint: Checkpoint):
        """Context manager to safely restore a checkpoint."""
        journal_backup = checkpoint.path / "journal.jsonl"
        narrative_backup = checkpoint.path / "narrative.log"
        
        if not journal_backup.exists():
            raise FileNotFoundError(f"Journal backup missing for checkpoint {checkpoint.checkpoint_id}")

        # Backup current state before restoration
        journal_backup_current = self.journal_path.with_suffix(".jsonl.bak")
        narrative_backup_current = self.narrative_path.with_suffix(".log.bak")
        
        try:
            if self.journal_path.exists():
                shutil.copy2(self.journal_path, journal_backup_current)
            if self.narrative_path.exists():
                shutil.copy2(self.narrative_path, narrative_backup_current)
            
            # Restore checkpoint files
            shutil.copy2(journal_backup, self.journal_path)
            if narrative_backup.exists():
                shutil.copy2(narrative_backup, self.narrative_path)
            
            log.info(f"Restored checkpoint {checkpoint.checkpoint_id} for cycle {checkpoint.cycle}")
            
            yield checkpoint
        finally:
            # Restore current state after context exits
            if journal_backup_current.exists():
                shutil.copy2(journal_backup_current, self.journal_path)
            if narrative_backup_current.exists():
                shutil.copy2(narrative_backup_current, self.narrative_path)
            log.info(f"Restored current journal state after checkpoint {checkpoint.checkpoint_id}")

    async def rollback_to_cycle(self, target_cycle: int) -> bool:
        """Rollback journal to the last known state at or before target_cycle."""
        # Find the most recent checkpoint at or before target_cycle
        checkpoints = await self.list_checkpoints()
        target_checkpoint = None
        
        for checkpoint in reversed(checkpoints):
            if checkpoint.cycle <= target_cycle:
                target_checkpoint = checkpoint
                break
        
        if not target_checkpoint:
            log.warning(f"No checkpoint found for cycle <= {target_cycle}")
            return False

        # Restore the checkpoint
        try:
            async with self.restore_checkpoint(target_checkpoint):
                # Record the rollback event
                self.record(
                    cycle=target_cycle,
                    event="ROLLBACK_COMPLETED",
                    category="milestone",
                    data={
                        "from_cycle": target_checkpoint.cycle,
                        "target_cycle": target_cycle,
                        "checkpoint_id": target_checkpoint.checkpoint_id,
                    },
                    narrative=f"Rolled back to cycle {target_cycle} via checkpoint {target_checkpoint.checkpoint_id}"
                )
            return True
        except Exception as e:
            log.error(f"Failed to rollback to cycle {target_cycle}: {e}")
            return False

    async def cleanup_old_checkpoints(self, keep_last: int = 5) -> int:
        """Remove old checkpoints, keeping only the most recent ones."""
        checkpoints = await self.list_checkpoints()
        if len(checkpoints) <= keep_last:
            return 0

        to_remove = checkpoints[:-keep_last]
        removed_count = 0
        
        for checkpoint in to_remove:
            try:
                shutil.rmtree(checkpoint.path, ignore_errors=True)
                removed_count += 1
                log.info(f"Removed old checkpoint {checkpoint.checkpoint_id}")
            except Exception as e:
                log.error(f"Failed to remove checkpoint {checkpoint.checkpoint_id}: {e}")

        return removed_count