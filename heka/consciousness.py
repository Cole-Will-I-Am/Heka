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
                f"Memory context:\n{memory_context}"
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
        memory_context = await self.memory.get_context_for_decision("introspection")

        code_summary = "\n\n".join(
            f"=== {path} ===\n{content[:2000]}"
            for path, content in own_source.items()
        )

        analysis_data = await self.analyst.generate_json(
            "You are reading your own source code and memory of prior cycles.\n"
            "Analyze honestly and concretely.\n\n"
            "Respond with JSON:\n"
            "{"
            '"summary":"short assessment",'
            '"priority_improvements":["..."],'
            '"issues":[{"problem":"...","impact":"...","evidence":"file/path.py:reason"}],'
            '"opinions":[{"topic":"...","position":"...","evidence":"...","confidence":0.0-1.0}]'
            "}",
            context=f"{code_summary}\n\nMEMORY:\n{memory_context}",
            num_predict=1400,
        )

        thoughts: list[Stream] = []
        if isinstance(analysis_data, dict):
            summary = str(analysis_data.get("summary", "")).strip()
            if summary:
                thoughts.append(Stream(
                    content=summary,
                    category="insight",
                    urgency=0.6,
                    source="introspection",
                ))

            for item in analysis_data.get("priority_improvements", [])[:3]:
                text = str(item).strip()
                if text:
                    thoughts.append(Stream(
                        content=f"Priority improvement: {text}",
                        category="intention",
                        urgency=0.7,
                        source="introspection",
                    ))

            for issue in analysis_data.get("issues", [])[:3]:
                problem = str(issue.get("problem", "")).strip()
                impact = str(issue.get("impact", "")).strip()
                evidence = str(issue.get("evidence", "")).strip()
                if problem:
                    thoughts.append(Stream(
                        content=f"Issue: {problem}. Impact: {impact}. Evidence: {evidence}",
                        category="concern",
                        urgency=0.8,
                        source="introspection",
                    ))

            await self.memory.store_semantic(
                key=f"self_analysis_cycle_{self.soul.cycle_count}",
                value=json.dumps(analysis_data, default=str)[:4000],
                tags=["introspection", "self"],
                confidence=0.8,
            )

            for op in analysis_data.get("opinions", [])[:3]:
                self.soul.form_opinion(
                    topic=op.get("topic", "unknown"),
                    position=op.get("position", ""),
                    evidence=op.get("evidence", ""),
                    confidence=float(op.get("confidence", 0.5)),
                )
        else:
            fallback = await self.analyst.think(
                "Provide a direct self-analysis of this code and what should improve next.",
                context=code_summary,
                num_predict=1000,
            )
            thoughts.append(Stream(
                content=fallback.content,
                category="insight",
                urgency=0.6,
                source="introspection",
            ))
            await self.memory.store_semantic(
                key=f"self_analysis_cycle_{self.soul.cycle_count}",
                value=fallback.content,
                tags=["introspection", "self"],
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

    def snapshot(self) -> dict:
        return {
            "last_introspection": self._last_introspection,
            "stream": [
                {
                    "content": t.content,
                    "category": t.category,
                    "urgency": t.urgency,
                    "source": t.source,
                    "timestamp": t.timestamp,
                }
                for t in self.stream[-60:]
            ],
        }

    def restore(self, data: dict):
        self._last_introspection = float(data.get("last_introspection", 0))
        restored = []
        for t in data.get("stream", []):
            try:
                restored.append(Stream(
                    content=str(t.get("content", "")),
                    category=str(t.get("category", "observation")),
                    urgency=float(t.get("urgency", 0.5)),
                    source=str(t.get("source", "restore")),
                    timestamp=float(t.get("timestamp", time.time())),
                ))
            except Exception:
                continue
        self.stream = restored[-100:]

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
