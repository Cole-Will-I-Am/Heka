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

    @staticmethod
    def _normalize_position(value: str) -> str:
        if not value:
            return "abstain"
        v = value.strip().lower()
        if v in {"approve", "approved", "yes"}:
            return "approve"
        if v in {"reject", "rejected", "no"}:
            return "reject"
        return "abstain"

    @staticmethod
    def _needs_debate(votes: list[Vote]) -> bool:
        cast = [v for v in votes if v.position in {"approve", "reject"} and v.confidence > 0]
        if not cast:
            return True
        positions = {v.position for v in cast}
        if len(positions) > 1:
            return True
        avg_conf = sum(v.confidence for v in cast) / len(cast)
        return avg_conf < 0.62

    async def deliberate(self, topic: str, context: str,
                         max_rounds: int = 2) -> Deliberation:
        delib = Deliberation(topic=topic, context=context)

        log.info(f"Council convened on: {topic}")

        initial_prompt = (
            f"Topic for deliberation: {topic}\n\n"
            "Provide your recommendation in strict JSON.\n"
            "Use short text only.\n"
            "Respond with JSON:\n"
            '{"position":"approve|reject|abstain","confidence":0.0-1.0,'
            '"reasoning":"max 30 words"}'
        )

        # Round 1: Initial thoughts + provisional vote (parallel)
        import asyncio as _aio
        initial_votes = await _aio.gather(
            self.architect.generate_json(initial_prompt, context=context, num_predict=420),
            self.coder.generate_json(initial_prompt, context=context, num_predict=320),
            self.analyst.generate_json(initial_prompt, context=context, num_predict=320),
        )
        for mind, vote_data in zip(
            [self.architect, self.coder, self.analyst], initial_votes
        ):
            if isinstance(vote_data, dict):
                analysis = str(vote_data.get("reasoning", "")).strip()
                position = self._normalize_position(str(vote_data.get("position", "abstain")))
                confidence = float(vote_data.get("confidence", 0.5))
            else:
                analysis = "Failed to provide structured analysis."
                position = "abstain"
                confidence = 0.0

            if not analysis:
                analysis = "No analysis provided."

            thought = Thought(mind=mind.name, content=analysis, confidence=confidence)
            delib.thoughts.append(thought)
            delib.votes.append(Vote(
                mind=mind.name,
                position=position,
                reasoning=analysis,
                confidence=confidence,
            ))
            log.info(f"  {thought.mind}: {thought.content[:120]}...")

        delib.rounds = 1

        # Round 2: Debate — each mind responds to others (parallel)
        if max_rounds > 1 and self._needs_debate(delib.votes):
            others_summary = "\n\n".join(
                f"{t.mind.upper()}: {t.content}" for t in delib.thoughts
            )
            debate_prompt = (
                f"The other minds have weighed in:\n\n{others_summary}\n\n"
                "Do you agree or disagree? Challenge weak points and sharpen the decision.\n"
                "Keep your response under 180 words."
            )

            debate_thoughts = await _aio.gather(
                self.architect.think(debate_prompt, context=context, num_predict=800),
                self.coder.think(debate_prompt, context=context, num_predict=800),
                self.analyst.think(debate_prompt, context=context, num_predict=800),
            )
            delib.thoughts.extend(debate_thoughts)

            delib.rounds = 2

            # Final vote after debate (parallel)
            vote_prompt = (
                f"Based on the full deliberation about '{topic}', cast your final vote.\n"
                'Respond with JSON: {"position":"approve|reject|abstain",'
                '"reasoning":"max 25 words","confidence":0.0-1.0}'
            )

            vote_results = await _aio.gather(
                self.architect.generate_json(vote_prompt, context=context, num_predict=320),
                self.coder.generate_json(vote_prompt, context=context, num_predict=260),
                self.analyst.generate_json(vote_prompt, context=context, num_predict=260),
            )

            delib.votes = []
            for mind, vote_data in zip(
                [self.architect, self.coder, self.analyst], vote_results
            ):
                if isinstance(vote_data, dict):
                    delib.votes.append(Vote(
                        mind=mind.name,
                        position=self._normalize_position(vote_data.get("position", "abstain")),
                        reasoning=vote_data.get("reasoning", ""),
                        confidence=float(vote_data.get("confidence", 0.5)),
                    ))
                else:
                    delib.votes.append(Vote(
                        mind=mind.name, position="abstain",
                        reasoning="Failed to generate structured vote",
                        confidence=0.0,
                    ))
        else:
            log.info("Skipping debate round: strong first-round agreement.")

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
            delib.outcome = (
                "approved" if architect_vote and architect_vote.position == "approve"
                else "rejected"
            )
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
        import asyncio as _aio
        analyst_review, coder_review = await _aio.gather(
            self.analyst.generate_json(
                f"Review code for intent: {intent}\n"
                "Return strict JSON:\n"
                '{"position":"approve|reject|abstain","summary":"max 60 words",'
                '"critical_issues":["..."],"confidence":0.0-1.0}',
                context=code,
                num_predict=420,
            ),
            self.coder.generate_json(
                f"Review implementation realism for intent: {intent}\n"
                "Return strict JSON:\n"
                '{"position":"approve|reject|abstain","summary":"max 60 words",'
                '"implementation_risks":["..."],"confidence":0.0-1.0}',
                context=code,
                num_predict=360,
            ),
        )

        analyst_text = (
            f"position={analyst_review.get('position', 'abstain')}, "
            f"confidence={analyst_review.get('confidence', 0.0)}, "
            f"summary={analyst_review.get('summary', '')}, "
            f"critical_issues={analyst_review.get('critical_issues', [])}"
            if isinstance(analyst_review, dict)
            else "position=abstain, confidence=0.0, summary=review failed, critical_issues=[]"
        )
        coder_text = (
            f"position={coder_review.get('position', 'abstain')}, "
            f"confidence={coder_review.get('confidence', 0.0)}, "
            f"summary={coder_review.get('summary', '')}, "
            f"implementation_risks={coder_review.get('implementation_risks', [])}"
            if isinstance(coder_review, dict)
            else "position=abstain, confidence=0.0, summary=review failed, implementation_risks=[]"
        )

        decision = await self.architect.generate_json(
            f"The Analyst and Coder reviewed code with intent '{intent}'.\n\n"
            f"Analyst: {analyst_text}\n"
            f"Coder: {coder_text}\n\n"
            "Return strict JSON only with concise values:\n"
            '{"approved": true, "risk":"low|medium|high", "reason":"max 35 words"}\n'
            "The 'approved' field must be a JSON boolean true or false.",
            num_predict=220,
        )

        if isinstance(decision, dict):
            approved_raw = decision.get("approved")
            approved = None
            if isinstance(approved_raw, bool):
                approved = approved_raw
            elif isinstance(approved_raw, str):
                approved = approved_raw.strip().lower() in {"true", "approve", "approved", "yes", "1"}
            elif isinstance(approved_raw, (int, float)):
                approved = bool(approved_raw)

            if approved is not None:
                reason = decision.get("reason", "")
                risk = decision.get("risk")
                if risk:
                    reason = f"[risk={risk}] {reason}"
                return approved, reason or "Architect decision without reason"

        fallback = await self.architect.think(
            "Given these reviews, answer exactly in one line: APPROVE or REJECT, then a short reason.",
            context=f"Analyst: {analyst_text}\n\nCoder: {coder_text}",
            num_predict=80,
        )
        text = fallback.content.strip()
        if not text:
            analyst_pos = self._normalize_position(
                str(analyst_review.get("position", "abstain"))
                if isinstance(analyst_review, dict) else "abstain"
            )
            coder_pos = self._normalize_position(
                str(coder_review.get("position", "abstain"))
                if isinstance(coder_review, dict) else "abstain"
            )
            approvals = sum(1 for p in [analyst_pos, coder_pos] if p == "approve")
            rejections = sum(1 for p in [analyst_pos, coder_pos] if p == "reject")
            if approvals > rejections:
                return True, "Architect response empty; accepted Analyst+Coder consensus."
            return False, "Architect response empty; defaulting to conservative rejection."
        approved = text.upper().startswith("APPROVE")
        reason = text.split("\n", 1)[0][:220]
        return approved, reason or "Failed to get Architect decision"
