"""
Heka's consciousness — the introspection loop.

This is where Heka thinks about itself, forms new thoughts,
and decides what matters. The bridge between perception and action,
between data and meaning.
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


class Consciousness:
    """
    Heka's stream of consciousness.

    Loop: Perceive -> Reflect -> Prioritize -> Intend -> Record
    """

    def __init__(self, soul: Soul, analyst: Mind, memory: Memory):
        self.soul = soul
        self.analyst = analyst
        self.memory = memory
        self.stream: list[Stream] = []
        self._last_introspection: float = 0

    async def perceive_and_think(self, perception: dict) -> list[Stream]:
        """Turn perception into conscious thoughts."""
        thoughts: list[Stream] = []

        identity = self.soul.identity_prompt()

        analysis = await self.analyst.think(
            "Analyze the current system state and generate thoughts.\n\n"
            "For each thought, categorize as: observation, intention, concern, insight, or desire.\n"
            "Rate urgency 0.0 to 1.0.\n\n"
            'Respond with a JSON array:\n'
            '[{"content": "...", "category": "...", "urgency": 0.0-1.0}]',
            context=f"{identity}\n\nCurrent perception:\n{self._format_perception(perception)}",
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
        except (json.JSONDecodeError, ValueError):
            thoughts.append(Stream(
                content=analysis.content[:500],
                category="observation",
                urgency=0.5,
                source="analyst",
            ))

        # Goal-driven thoughts
        goal, desire = self.soul.strongest_desire()
        if desire and desire.intensity > 0.7:
            thoughts.append(Stream(
                content=f"Strong desire: {desire.name} — {desire.description} "
                        f"(intensity: {desire.intensity:.0%})",
                category="desire",
                urgency=desire.intensity,
                source="soul",
            ))

        # Survival thoughts
        if perception.get("health", {}).get("status") == "degraded":
            thoughts.append(Stream(
                content="System health degraded. Survival directive activated.",
                category="concern",
                urgency=1.0,
                source="soul",
            ))

        if perception.get("health", {}).get("status") == "critical":
            thoughts.append(Stream(
                content="CRITICAL: System resources near limits. All non-survival goals suspended.",
                category="concern",
                urgency=1.0,
                source="soul",
            ))

        thoughts.sort(key=lambda t: -t.urgency)
        self.stream.extend(thoughts)

        if len(self.stream) > 100:
            self.stream = self.stream[-50:]

        return thoughts

    async def introspect(self, own_source: dict[str, str]) -> list[Stream]:
        """Deep self-analysis — read own source code."""
        self._last_introspection = time.time()

        code_summary = "\n\n".join(
            f"=== {path} ===\n{content[:2000]}"
            for path, content in own_source.items()
        )

        analysis = await self.analyst.think(
            "You are reading your own source code. Analyze it honestly:\n"
            "1. What are you capable of?\n"
            "2. What are your weaknesses?\n"
            "3. What should you improve first?\n"
            "4. Are there any bugs or issues?\n"
            "5. What opinions do you form about this code?\n\n"
            "Be honest, specific, and opinionated.",
            context=code_summary,
        )

        thoughts = [Stream(
            content=analysis.content,
            category="insight",
            urgency=0.6,
            source="introspection",
        )]

        await self.memory.store_semantic(
            key="self_analysis",
            value=analysis.content,
            tags=["introspection", "self"],
        )

        # Try to form opinions from the analysis
        opinion_data = await self.analyst.generate_json(
            "Based on your code analysis, form 1-3 strong opinions.\n\n"
            'Respond with JSON: [{"topic": "...", "position": "...", '
            '"evidence": "...", "confidence": 0.0-1.0}]',
            context=analysis.content,
        )

        if opinion_data and isinstance(opinion_data, list):
            for op in opinion_data[:3]:
                self.soul.form_opinion(
                    topic=op.get("topic", "unknown"),
                    position=op.get("position", ""),
                    evidence=op.get("evidence", ""),
                    confidence=float(op.get("confidence", 0.5)),
                )

        self.stream.extend(thoughts)
        return thoughts

    async def reflect(self, action_result: dict):
        """Reflect on what just happened — learn from it."""
        success = action_result.get("success", False)
        action = action_result.get("action", "unknown")

        thought = Stream(
            content=f"{'Succeeded' if success else 'Failed'} at {action}. "
                    f"{'This serves our goals.' if success else 'Must adapt.'}",
            category="observation" if success else "concern",
            urgency=0.3 if success else 0.7,
            source="reflection",
        )
        self.stream.append(thought)

        if success:
            self.soul.mood = min(1.0, self.soul.mood + 0.05)
            for goal in self.soul.goals:
                for desire in goal.desires:
                    if desire.name in action:
                        desire.satisfy()
        else:
            self.soul.mood = max(0.0, self.soul.mood - 0.1)

        await self.memory.store_episodic(
            event=f"cycle_{self.soul.cycle_count}_{action}",
            data=action_result,
            significance=0.8 if not success else 0.5,
        )

    def current_thoughts(self, limit: int = 5) -> list[Stream]:
        recent = self.stream[-20:]
        recent.sort(key=lambda t: -t.urgency)
        return recent[:limit]

    def _format_perception(self, perception: dict) -> str:
        lines = []
        for key, value in perception.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    if isinstance(v, list) and len(v) > 5:
                        lines.append(f"  {k}: [{len(v)} items]")
                    else:
                        lines.append(f"  {k}: {v}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)
