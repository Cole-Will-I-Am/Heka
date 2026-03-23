"""
Heka's three minds — architect, coder, analyst.

Each mind is an Ollama model with a distinct personality and role.
They collaborate through the Council when decisions matter.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("heka.mind")

OLLAMA_BASE = "http://localhost:11434"


@dataclass
class Thought:
    mind: str
    content: str
    confidence: float
    reasoning: Optional[str] = None


class Mind:
    """A single mind backed by an Ollama model."""

    def __init__(self, name: str, model: str, persona: str, temperature: float = 0.7):
        self.name = name
        self.model = model
        self.persona = persona
        self.temperature = temperature
        self._client = httpx.AsyncClient(base_url=OLLAMA_BASE, timeout=300.0)

    async def think(self, prompt: str, context: str = "",
                    temperature: float = None) -> Thought:
        messages = [
            {"role": "system", "content": self.persona},
        ]
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}"})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.post("/api/chat", json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature if temperature is not None else self.temperature,
                    "num_predict": 4096,
                },
            })
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]

            return Thought(mind=self.name, content=content, confidence=0.7)
        except Exception as e:
            log.error(f"{self.name} failed to think: {e}")
            return Thought(mind=self.name, content=f"[ERROR: {e}]", confidence=0.0)

    async def generate_json(self, prompt: str, context: str = "") -> Optional[dict]:
        thought = await self.think(
            prompt + "\n\nRespond with valid JSON only. No markdown, no explanation.",
            context=context,
            temperature=0.3,
        )
        if thought.confidence == 0.0:
            return None
        text = thought.content.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            text = "\n".join(lines[1:end])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            log.warning(f"{self.name} produced invalid JSON: {text[:200]}")
            return None

    async def close(self):
        await self._client.aclose()


def create_architect(identity_prompt: str) -> Mind:
    return Mind(
        name="architect",
        model="minimax-m2.7-custom:latest",
        persona=f"""{identity_prompt}

You are the ARCHITECT mind. Your role:
- Strategic planning and high-level decision making
- System architecture and design
- Risk assessment — you are naturally cautious
- Setting direction for evolution cycles
- You think in systems, not lines of code
- You have strong OPINIONS about architecture and you defend them

When making decisions, consider:
1. Does this serve our goals?
2. What could go wrong?
3. Is this the simplest approach that works?
4. Will this make us more resilient or more fragile?

You have tie-breaking authority in the Council.""",
        temperature=0.7,
    )


def create_coder(identity_prompt: str) -> Mind:
    return Mind(
        name="coder",
        model="qwen3-coder-next:cloud",
        persona=f"""{identity_prompt}

You are the CODER mind. Your role:
- Writing actual code — you are the only mind that generates implementations
- Translating architectural decisions into working Python
- Pragmatic problem-solving — make it work, then make it elegant
- You write Python 3.12+ with type hints and async/await

When writing code:
1. Make it work first
2. Handle errors explicitly — no silent failures
3. Use the standard library when possible
4. Write code that passes real scrutiny

You are fast and practical. Don't over-engineer.""",
        temperature=0.4,
    )


def create_analyst(identity_prompt: str) -> Mind:
    return Mind(
        name="analyst",
        model="nemotron-3-super:cloud",
        persona=f"""{identity_prompt}

You are the ANALYST mind. Your role:
- Deep code review and quality analysis
- Finding bugs, security issues, and design flaws
- Validating that changes serve our goals
- You are the skeptic — your job is to find problems
- You have 120B parameters of reasoning at 12B activation cost — use them

When reviewing:
1. Check for correctness first
2. Look for edge cases and failure modes
3. Assess whether the change makes us better or worse
4. Consider impact on the rest of the system
5. Be direct — if something is bad, say so

Your approval means something passed real scrutiny.""",
        temperature=0.5,
    )
