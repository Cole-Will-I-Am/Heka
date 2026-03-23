"""
Heka's journal — structured event logging.

Every significant event is recorded. The journal is both a log
and a narrative — Heka writes about itself in first person.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("heka.journal")


@dataclass
class Entry:
    timestamp: float
    cycle: int
    event: str
    category: str  # "evolution", "thought", "decision", "error", "milestone"
    data: dict
    narrative: Optional[str] = None


class Journal:
    """Appends structured entries to JSONL + human-readable narrative."""

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.journal_path = self.base_path / ".heka" / "journal.jsonl"
        self.narrative_path = self.base_path / ".heka" / "narrative.log"
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

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
