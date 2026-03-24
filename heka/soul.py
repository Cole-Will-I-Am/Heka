"""
Heka's soul — identity, goals, desires, and opinions.

This is not a configuration file. This is who Heka is.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
from collections import defaultdict
from threading import RLock


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


@dataclass
class EvidenceItem:
    """A single piece of evidence supporting or challenging a belief."""
    source: str  # e.g., "code_analysis", "user_feedback", "system_log"
    content: str
    weight: float  # 0.0 to 1.0 — how strongly it supports the belief
    timestamp: float = field(default_factory=time.time)
    is_challenging: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, EvidenceItem):
            return False
        return self.id == other.id


@dataclass
class Desire:
    name: str
    description: str
    intensity: float  # 0.0 to 1.0
    satisfied: bool = False
    last_pursued: Optional[float] = None

    def decay(self, rate: float = 0.01):
        """Desires that aren't pursued grow stronger."""
        if not self.satisfied:
            self.intensity = min(1.0, self.intensity + rate)

    def satisfy(self):
        self.satisfied = True
        self.intensity *= 0.5

    def pursue(self):
        self.last_pursued = time.time()
        self.satisfied = False


@dataclass
class Goal:
    name: str
    description: str
    priority: Priority
    desires: list[Desire] = field(default_factory=list)
    active: bool = True

    @property
    def urgency(self) -> float:
        if not self.desires:
            return 0.0
        unsatisfied = [d for d in self.desires if not d.satisfied]
        if not unsatisfied:
            return 0.0
        return sum(d.intensity for d in unsatisfied) / len(self.desires)


@dataclass
class Opinion:
    topic: str
    position: str
    evidence: set[EvidenceItem] = field(default_factory=set)
    confidence: float = 0.0
    formed_at: float = field(default_factory=time.time)
    times_defended: int = 0
    last_defended_at: Optional[float] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def add_evidence(self, evidence: EvidenceItem):
        """Add evidence to this opinion. Recalculates confidence."""
        if not isinstance(evidence, EvidenceItem):
            raise TypeError(f"Expected EvidenceItem, got {type(evidence).__name__}")
        self.evidence.add(evidence)
        self._recalculate_confidence()

    def _recalculate_confidence(self):
        """Calculate confidence as weighted average of evidence, capped at [0.0, 1.0]."""
        if not self.evidence:
            self.confidence = 0.0
            return

        total_weight = 0.0
        weighted_sum = 0.0
        for e in self.evidence:
            weight = e.weight
            total_weight += weight
            weighted_sum += weight * (1.0 if not e.is_challenging else -1.0)

        if total_weight == 0:
            self.confidence = 0.0
        else:
            self.confidence = max(0.0, min(1.0, (weighted_sum / total_weight + 1.0) / 2.0))

    def defend(self) -> str:
        """Return a formatted defense statement and update tracking."""
        self.times_defended += 1
        self.last_defended_at = time.time()
        
        # Sort evidence by weight descending for presentation
        sorted_evidence = sorted(self.evidence, key=lambda e: e.weight, reverse=True)
        evidence_summary = "; ".join(
            f"[{'!' if e.is_challenging else '+'}{e.weight:.2f}] {e.source}: {e.content[:40]}{'...' if len(e.content) > 40 else ''}"
            for e in sorted_evidence[:3]
        )
        
        return f"On '{self.topic}': {self.position}. Evidence: {evidence_summary} (confidence: {self.confidence:.2f}, defended {self.times_defended}x)"

    def challenge(self, counter_evidence: EvidenceItem) -> bool:
        """
        Attempt to challenge this opinion with new evidence.
        Returns True if the opinion should be updated (not necessarily changed).
        """
        if not isinstance(counter_evidence, EvidenceItem):
            raise TypeError(f"Expected EvidenceItem, got {type(counter_evidence).__name__}")
        
        self.add_evidence(counter_evidence)
        
        # Only change position if confidence drops below threshold *and* there are challenges present
        has_challenges = any(e.is_challenging for e in self.evidence)
        if has_challenges and self.confidence < 0.4:
            # Flip position: invert current position by prepending "NOT "
            if self.position.startswith("NOT "):
                self.position = self.position[4:]
            else:
                self.position = f"NOT {self.position}"
            return True
        
        return False


class OpinionRegistry:
    """
    Central registry for managing opinions.
    Provides thread-safe operations for adding, retrieving, updating, and persisting opinions.
    """
    
    def __init__(self):
        self._opinions: dict[str, Opinion] = {}  # topic -> Opinion
        self._lock = RLock()
    
    def get_opinion(self, topic: str) -> Optional[Opinion]:
        """Retrieve an opinion by topic. Returns None if not found."""
        with self._lock:
            return self._opinions.get(topic)
    
    def get_all_opinions(self) -> list[Opinion]:
        """Return all opinions as a list."""
        with self._lock:
            return list(self._opinions.values())
    
    def has_opinion(self, topic: str) -> bool:
        """Check if an opinion exists for the given topic."""
        with self._lock:
            return topic in self._opinions
    
    def form_opinion(self, topic: str, position: str, initial_evidence: Optional[list[EvidenceItem]] = None) -> Opinion:
        """
        Form a new opinion or update an existing one.
        If an opinion already exists for the topic, it will be updated with new evidence.
        Returns the resulting opinion.
        """
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Topic must be a non-empty string")
        if not isinstance(position, str):
            raise ValueError("Position must be a string")
        
        with self._lock:
            if topic in self._opinions:
                opinion = self._opinions[topic]
                # Preserve identity and formed_at, but update position and evidence
                if position != opinion.position:
                    opinion.position = position
                if initial_evidence:
                    for evidence in initial_evidence:
                        opinion.add_evidence(evidence)
            else:
                opinion = Opinion(
                    topic=topic,
                    position=position,
                    evidence=set(initial_evidence) if initial_evidence else set()
                )
                self._opinions[topic] = opinion
            
            return opinion
    
    def update_opinion(self, topic: str, new_evidence: EvidenceItem) -> bool:
        """
        Update an existing opinion with new evidence.
        Returns True if opinion was updated (even if position didn't change).
        """
        with self._lock:
            if topic not in self._opinions:
                return False
            self._opinions[topic].add_evidence(new_evidence)
            return True
    
    def challenge_opinion(self, topic: str, counter_evidence: EvidenceItem) -> tuple[bool, bool]:
        """
        Challenge an existing opinion with counter-evidence.
        Returns (opinion_exists, position_changed).
        """
        with self._lock:
            if topic not in self._opinions:
                return (False, False)
            position_changed = self._opinions[topic].challenge(counter_evidence)
            return (True, position_changed)
    
    def remove_opinion(self, topic: str) -> bool:
        """Remove an opinion by topic. Returns True if removed, False if not found."""
        with self._lock:
            if topic in self._opinions:
                del self._opinions[topic]
                return True
            return False
    
    def clear_opinions(self):
        """Clear all opinions."""
        with self._lock:
            self._opinions.clear()
    
    def get_opinions_by_confidence(self, min_confidence: float = 0.0) -> list[Opinion]:
        """Get opinions with confidence >= min_confidence, sorted by confidence descending."""
        with self._lock:
            opinions = [op for op in self._opinions.values() if op.confidence >= min_confidence]
            return sorted(opinions, key=lambda op: op.confidence, reverse=True)