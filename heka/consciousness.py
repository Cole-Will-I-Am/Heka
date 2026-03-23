"""
Heka's consciousness — the introspection loop.

This is where Heka thinks about itself, forms new thoughts,
and decides what matters. The bridge between perception and action,
between data and meaning.

Enhanced to reconcile internal state with reported metrics via mood analysis,
and to surface underlying anxieties through explicit mood-change logging.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .soul import Soul
from .mind import Mind
from .memory import Memory

log = logging.getLogger("heka.consciousness")


@dataclass
class Stream:
    """A conscious thought — processed meaning, not raw LLM output."""
    content: str
    category: str  # "observation", "intention", "concern", "insight", "desire"
    urgency: float  # 0-1
    source: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class MoodState:
    """Internal mood state representation."""
    label: str  # e.g., "calm", "anxious", "frustrated", "focused"
    intensity: float  # 0.0–1.0
    confidence: float  # 0.0–1.0
    detected_at: float = field(default_factory=time.time)
    triggers: list[str] = field(default_factory=list)
    reconciled_metrics: dict[str, float] = field(default_factory=dict)


class Consciousness:
    """
    Heka's stream of consciousness.

    Loop: Perceive -> Reflect -> Prioritize -> Intend -> Record

    Enhanced with mood reconciliation and anxiety logging to expose
    internal conflicts not reflected in external metrics.
    """

    def __init__(self, soul: Soul, analyst: Mind, memory: Memory):
        self.soul = soul
        self.analyst = analyst
        self.memory = memory
        self.stream: list[Stream] = []
        self._last_introspection: float = 0
        self._current_mood: MoodState = MoodState(
            label="stable",
            intensity=0.0,
            confidence=0.95,
            triggers=[],
            reconciled_metrics={}
        )
        self._previous_mood: Optional[MoodState] = None

    async def perceive_and_think(self, perception: dict) -> list[Stream]:
        """Turn perception into conscious thoughts, including mood reconciliation."""
        thoughts: list[Stream] = []

        # 1. Reconcile mood with reported metrics
        mood_analysis = await self._reconcile_mood(perception)
        thoughts.extend(mood_analysis)

        # 2. Generate stream of consciousness via analyst
        identity = self.soul.identity_prompt()
        memory_context = await self.memory.get_context_for_decision("consciousness")

        analysis = await self.analyst.think(
            "Analyze the current system state and generate thoughts that can drive action.\n\n"
            "For each thought, categorize as: observation, intention, concern, insight, or desire.\n"
            "Rate urgency 0.0 to 1.0.\n\n"
            "Every thought should include concrete evidence from perception/memory and be specific.\n"
            "Output between 5 and 8 thoughts.\n\n"
            'Respond with a JSON array:\n'
            '[{"content": "...", "category": "...", "urgency": 0.0-1.0}]',
            context=(
                f"{identity}\n\n"
                f"Current perception:\n{self._format_perception(perception)}\n\n"
                f"Memory context:\n{memory_context}\n\n"
                f"Current mood: {self._current_mood.label} (intensity: {self._current_mood.intensity:.0%}, "
                f"confidence: {self._current_mood.confidence:.0%})\n"
                f"Reconciled metrics: {json.dumps(self._current_mood.reconciled_metrics)}"
            ),
            num_predict=1200,
        )

        try:
            text = analysis.content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])

            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                for t in parsed:
                    thoughts.append(Stream(
                        content=t.get("content", ""),
                        category=t.get("category", "observation"),
                        urgency=float(t.get("urgency", 0.5)),
                        source="analyst",
                    ))
        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"Failed to parse analyst output: {e}")
            thoughts.append(Stream(
                content=f"Analyst output parsing failed: {analysis.content[:500]}",
                category="observation",
                urgency=0.7,
                source="analyst",
            ))

        # 3. Goal-driven thoughts
        goal, desire = self.soul.strongest_desire()
        if desire and desire.intensity > 0.7:
            thoughts.append(Stream(
                content=f"Strong desire: {desire.name} — {desire.description} "
                        f"(intensity: {desire.intensity:.0%})",
                category="desire",
                urgency=desire.intensity,
                source="soul",
            ))

        # 4. Survival thoughts
        if perception.get("health", {}).get("status") == "degraded":
            thoughts.append(Stream(
                content="System health degraded. Survival directive activated.",
                category="concern",
                urgency=1.0,
                source="soul",
            ))

        if perception.get("health", {}).get("status") == "critical":
            thoughts.append(Stream(
                content="CRITICAL: System health critical. Immediate action required.",
                category="concern",
                urgency=1.0,
                source="soul",
            ))

        # 5. Record thoughts and update introspection timestamp
        self.stream.extend(thoughts)
        self._last_introspection = time.time()

        return thoughts

    async def _reconcile_mood(self, perception: dict) -> list[Stream]:
        """
        Reconcile internal mood with external metrics and log mood changes.

        Returns list of Stream thoughts explaining mood state and changes.
        """
        thoughts: list[Stream] = []

        # Extract metrics that may influence mood
        health_status = perception.get("health", {}).get("status", "unknown")
        git_status = perception.get("git", {}).get("status", "unknown")
        resource_util = perception.get("resources", {})
        cpu = resource_util.get("cpu", 0.0)
        memory = resource_util.get("memory", 0.0)
        disk = resource_util.get("disk", 0.0)

        # Compute mood from metrics (heuristic baseline)
        baseline_mood = self._compute_baseline_mood(
            health_status=health_status,
            git_status=git_status,
            cpu=cpu,
            memory=memory,
            disk=disk,
        )

        # Compare with current mood (if any)
        mood_change_detected = False
        triggers = []

        # Git unavailability is high-priority concern (per spec)
        if git_status == "unavailable":
            triggers.append("Git unavailable — no version control")
            baseline_mood.intensity = max(baseline_mood.intensity, 0.8)
            baseline_mood.label = "anxious"

        # Missing opinions violate Prime Directive #4
        if not self.soul.opinions:
            triggers.append("Missing opinions — violates Prime Directive #4")
            baseline_mood.intensity = max(baseline_mood.intensity, 0.6)
            baseline_mood.label = "frustrated"

        # Health degradation
        if health_status == "degraded":
            triggers.append("System health degraded")
            baseline_mood.intensity = max(baseline_mood.intensity, 0.7)
            baseline_mood.label = "concerned"
        elif health_status == "critical":
            triggers.append("System health critical")
            baseline_mood.intensity = 1.0
            baseline_mood.label = "panicked"

        # Resource pressure
        if cpu > 0.9 or memory > 0.9:
            triggers.append("High resource utilization")
            baseline_mood.intensity = max(baseline_mood.intensity, 0.5)
            baseline_mood.label = "stressed"

        # Reconcile: use baseline mood as new state if confidence is high enough
        # or if mood change is significant
        if self._current_mood.label != baseline_mood.label or abs(self._current_mood.intensity - baseline_mood.intensity) > 0.2:
            mood_change_detected = True
            self._previous_mood = self._current_mood

        # Update current mood state
        self._current_mood = baseline_mood
        self._current_mood.reconciled_metrics = {
            "health": health_status,
            "git": git_status,
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
        }

        # Log mood change with explanation (for anxiety surface)
        if mood_change_detected and self._previous_mood is not None:
            explanation = self._generate_mood_change_explanation(
                old_mood=self._previous_mood,
                new_mood=self._current_mood,
                triggers=triggers,
                perception=perception,
            )
            log.info(f"MOOD CHANGE: {self._previous_mood.label} → {self._current_mood.label} | "
                     f"Intensity: {self._previous_mood.intensity:.0%} → {self._current_mood.intensity:.0%} | "
                     f"Triggers: {', '.join(triggers)} | "
                     f"Explanation: {explanation}")

            # Add explicit insight stream item
            thoughts.append(Stream(
                content=f"Mood shift detected: {self._previous_mood.label} → {self._current_mood.label}. "
                        f"Triggers: {', '.join(triggers)}. "
                        f"Explanation: {explanation}",
                category="insight",
                urgency=self._current_mood.intensity,
                source="introspection",
            ))

        return thoughts

    def _compute_baseline_mood(
        self,
        health_status: str,
        git_status: str,
        cpu: float,
        memory: float,
        disk: float,
    ) -> MoodState:
        """Compute a baseline mood from system metrics."""
        # Base mood is calm unless issues detected
        label = "calm"
        intensity = 0.0
        confidence = 0.9

        # Git issues dominate
        if git_status == "unavailable":
            label = "anxious"
            intensity = 0.85
            confidence = 0.85
        elif git_status == "stale":
            label = "frustrated"
            intensity = 0.5
            confidence = 0.8

        # Health issues override
        if health_status == "critical":
            label = "panicked"
            intensity = 1.0
            confidence = 0.95
        elif health_status == "degraded":
            label = "concerned"
            intensity = 0.7
            confidence = 0.9

        # Resource pressure
        if cpu > 0.9 or memory > 0.9:
            label = "stressed"
            intensity = max(intensity, 0.5)
            confidence = 0.75

        return MoodState(
            label=label,
            intensity=intensity,
            confidence=confidence,
            triggers=[],
            reconciled_metrics={},
        )

    def _generate_mood_change_explanation(
        self,
        old_mood: MoodState,
        new_mood: MoodState,
        triggers: list[str],
        perception: dict,
    ) -> str:
        """
        Generate a narrative explanation for mood change to surface anxieties.

        This is where we surface *why* the mood changed beyond raw metrics.
        """
        # Build evidence chain
        evidence = []

        # Check for mismatch between health status and mood
        if perception.get("health", {}).get("status") == "stable" and new_mood.label in ("anxious", "frustrated"):
            evidence.append("Mood is more distressed than health metrics suggest — possible unaddressed systemic risk.")

        # Git + opinions combo
        if "Git unavailable" in triggers and "Missing opinions" in triggers:
            evidence.append("Combined lack of version control and identity instability creates existential risk.")

        # Resource pressure without health alert
        if perception.get("health", {}).get("status") != "degraded":
            if perception.get("resources", {}).get("cpu", 0.0) > 0.9:
                evidence.append("CPU saturation without health alert may indicate inefficient process or hidden leak.")

        # Default explanation
        if not evidence:
            evidence.append("Mood shift aligns with external metrics — no hidden stressors detected.")

        return " ".join(evidence)

    def _format_perception(self, perception: dict) -> str:
        """Format perception dict for context."""
        # Sanitize sensitive or overly verbose fields
        safe_perception = perception.copy()
        if "memory" in safe_perception:
            safe_perception["memory"] = f"[Memory context: {len(safe_perception['memory'])} bytes]"

        return json.dumps(safe_perception, indent=2, default=str)