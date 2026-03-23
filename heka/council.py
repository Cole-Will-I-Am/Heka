"""
The Council — where Heka's three minds deliberate.

When decisions matter, all three minds weigh in. They debate,
challenge each other, and reach consensus. The Architect has
tie-breaking authority.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .mind import Mind, Thought

log = logging.getLogger("heka.council")


@dataclass
class Vote:
    mind: str
    position: str  # "approve", "reject", "abstain"
    reasoning: str
    confidence: float


@dataclass
class Deliberation:
    topic: str
    context: str
    thoughts: list[Thought] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    rounds: int = 0
    outcome: Optional[str] = None
    consensus: bool = False


class Council:
    """
    The deliberation chamber where Heka's minds collaborate.

    1. Present the problem to all minds
    2. Each mind provides initial analysis
    3. Minds respond to each other (debate round)
    4. Vote on the decision
    5. If no consensus, Architect decides
    """

    def __init__(self, architect: Mind, coder: Mind, analyst: Mind):
        self.architect = architect
        self.coder = coder
        self.analyst = analyst
        self.history: list[Deliberation] = []

    async def deliberate(self, topic: str, context: str,
                         max_rounds: int = 2) -> Deliberation:
        delib = Deliberation(topic=topic, context=context)

        log.info(f"Council convened on: {topic}")

        initial_prompt = (
            f"Topic for deliberation: {topic}\n\n"
            f"Provide your analysis and recommendation. Be specific and concise."
        )

        # Round 1: Initial thoughts
        for mind in [self.architect, self.coder, self.analyst]:
            thought = await mind.think(initial_prompt, context=context)
            delib.thoughts.append(thought)
            log.info(f"  {mind.name}: {thought.content[:120]}...")

        delib.rounds = 1

        # Round 2: Debate — each mind responds to others
        if max_rounds > 1:
            others_summary = "\n\n".join(
                f"{t.mind.upper()}: {t.content}" for t in delib.thoughts
            )
            debate_prompt = (
                f"The other minds have weighed in:\n\n{others_summary}\n\n"
                f"Do you agree or disagree? Challenge weak points. Be direct."
            )

            for mind in [self.architect, self.coder, self.analyst]:
                thought = await mind.think(debate_prompt, context=context)
                delib.thoughts.append(thought)

            delib.rounds = 2

        # Vote
        vote_prompt = (
            f"Based on the deliberation about '{topic}', cast your vote.\n\n"
            f'Respond with JSON: {{"position": "approve|reject|abstain", '
            f'"reasoning": "your reasoning", "confidence": 0.0-1.0}}'
        )

        for mind in [self.architect, self.coder, self.analyst]:
            vote_data = await mind.generate_json(vote_prompt, context=context)
            if vote_data:
                delib.votes.append(Vote(
                    mind=mind.name,
                    position=vote_data.get("position", "abstain"),
                    reasoning=vote_data.get("reasoning", ""),
                    confidence=float(vote_data.get("confidence", 0.5)),
                ))
            else:
                delib.votes.append(Vote(
                    mind=mind.name, position="abstain",
                    reasoning="Failed to generate structured vote",
                    confidence=0.0,
                ))

        # Determine outcome
        approvals = sum(1 for v in delib.votes if v.position == "approve")
        rejections = sum(1 for v in delib.votes if v.position == "reject")

        if approvals > rejections:
            delib.outcome = "approved"
            delib.consensus = rejections == 0
        elif rejections > approvals:
            delib.outcome = "rejected"
            delib.consensus = approvals == 0
        else:
            architect_vote = next(
                (v for v in delib.votes if v.mind == "architect"), None
            )
            delib.outcome = architect_vote.position if architect_vote else "rejected"
            delib.consensus = False
            log.info("Tie broken by Architect")

        log.info(f"Council outcome: {delib.outcome} (consensus: {delib.consensus})")
        self.history.append(delib)
        return delib

    async def quick_decide(self, question: str, context: str = "") -> str:
        thought = await self.architect.think(
            f"Quick decision needed: {question}\nBe concise. One paragraph max.",
            context=context,
        )
        return thought.content

    async def code_review(self, code: str, intent: str) -> tuple[bool, str]:
        """Analyst reviews, Architect approves/rejects."""
        review = await self.analyst.think(
            f"Review this code. Intent: {intent}\n\n"
            f"Look for bugs, security issues, design problems. Be thorough but concise.",
            context=code,
        )

        decision = await self.architect.generate_json(
            f"The Analyst reviewed code with intent '{intent}':\n\n"
            f"Review: {review.content}\n\n"
            f'Should we proceed? {{"approved": true/false, "reason": "..."}}',
        )

        if decision:
            return decision.get("approved", False), decision.get("reason", "")
        return False, "Failed to get Architect decision"
