"""
Heka's evolution engine — plan, execute, validate, rollback.

Every code change goes through:
1. Architect plans it
2. Coder writes it
3. Analyst reviews it
4. Council approves it
5. Executor applies it with backup + rollback
"""

import ast
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .mind import Mind
from .council import Council
from .memory import Memory

log = logging.getLogger("heka.evolution")


@dataclass
class EvolutionPlan:
    action: str  # "create", "modify", "delete", "refactor"
    files: list[dict]  # [{"path": str, "content": str, "intent": str}]
    reasoning: str
    goal_alignment: str
    risk_level: str  # "low", "medium", "high"
    confidence: float
    cycle: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class EvolutionResult:
    success: bool
    action: str
    files_affected: list[str]
    error: Optional[str] = None
    review_passed: bool = False
    rollback_performed: bool = False


class Evolution:
    """Changes don't happen without going through here."""

    def __init__(self, base_path: Path, architect: Mind, coder: Mind,
                 council: Council, memory: Memory):
        self.base_path = base_path
        self.architect = architect
        self.coder = coder
        self.council = council
        self.memory = memory
        self.backup_dir = base_path / ".heka" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def plan(self, thoughts: list, perception: dict,
                   cycle: int) -> Optional[EvolutionPlan]:
        """Architect creates an evolution plan from current thoughts."""
        memory_context = await self.memory.get_context_for_decision("evolution")

        thoughts_text = "\n".join(
            f"  [{t.category}] (urgency {t.urgency:.0%}) {t.content}"
            for t in thoughts[:10]
        )

        perception_text = json.dumps(perception, indent=2, default=str)[:3000]

        prompt = f"""Based on my current thoughts and system state, create an evolution plan.

MY CURRENT THOUGHTS:
{thoughts_text}

SYSTEM STATE:
{perception_text}

MEMORY:
{memory_context}

Create a plan. Respond with JSON:
{{
    "action": "create|modify|refactor|delete",
    "files": [
        {{"path": "relative/path.py", "intent": "what this file should do"}}
    ],
    "reasoning": "why this evolution",
    "goal_alignment": "which goal this serves",
    "risk_level": "low|medium|high",
    "confidence": 0.0-1.0
}}

Rules:
- Maximum 3 files per plan
- Prefer modify over create (build on what exists)
- Higher confidence = simpler, safer changes
- If nothing needs changing, set confidence to 0
- NEVER create files with placeholder content
- Every file must have real, functional code"""

        plan_data = await self.architect.generate_json(prompt)

        if not plan_data or plan_data.get("confidence", 0) < 0.3:
            log.info("Architect confidence too low — skipping evolution")
            return None

        files = plan_data.get("files", [])
        if not files or len(files) > 3:
            log.warning("Invalid file count in plan — skipping")
            return None

        return EvolutionPlan(
            action=plan_data.get("action", "modify"),
            files=files,
            reasoning=plan_data.get("reasoning", ""),
            goal_alignment=plan_data.get("goal_alignment", ""),
            risk_level=plan_data.get("risk_level", "low"),
            confidence=plan_data.get("confidence", 0.5),
            cycle=cycle,
        )

    async def implement(self, plan: EvolutionPlan) -> Optional[dict[str, str]]:
        """Coder writes the actual code for a plan."""
        implementations = {}

        for file_spec in plan.files:
            path = file_spec.get("path", "")
            intent = file_spec.get("intent", "")

            if not path or not intent:
                continue

            full_path = self.base_path / path
            existing = ""
            if full_path.exists():
                existing = full_path.read_text(errors="replace")

            if plan.action == "delete":
                implementations[path] = None
                continue

            prompt = f"""Write Python code for: {intent}

File: {path}
Action: {plan.action}
Reasoning: {plan.reasoning}

{"EXISTING CODE TO MODIFY:" if existing else "Write a new file from scratch:"}
{existing[:4000] if existing else ""}

Requirements:
- Python 3.12+, type hints, async where appropriate
- Handle errors explicitly
- Include a module docstring
- Production quality — this will run autonomously
- Output ONLY the complete file content, nothing else"""

            thought = await self.coder.think(prompt)
            code = thought.content.strip()

            # Strip markdown fences
            if code.startswith("```"):
                lines = code.split("\n")
                end = len(lines)
                for i in range(len(lines) - 1, 0, -1):
                    if lines[i].strip().startswith("```"):
                        end = i
                        break
                code = "\n".join(lines[1:end])

            # Validate Python syntax
            if path.endswith(".py"):
                try:
                    ast.parse(code)
                except SyntaxError as e:
                    log.warning(f"Coder produced invalid Python for {path}: {e}")
                    # One retry with the error
                    retry = await self.coder.think(
                        f"Your previous code had a syntax error: {e}\n\n"
                        f"Fix it. Output ONLY the corrected complete file.",
                        context=code,
                    )
                    code = retry.content.strip()
                    if code.startswith("```"):
                        lines = code.split("\n")
                        end = len(lines)
                        for i in range(len(lines) - 1, 0, -1):
                            if lines[i].strip().startswith("```"):
                                end = i
                                break
                        code = "\n".join(lines[1:end])
                    try:
                        ast.parse(code)
                    except SyntaxError:
                        log.error(f"Retry also produced invalid Python for {path}")
                        continue

            # Reject degenerate output
            stripped_lines = [l for l in code.split("\n") if l.strip()]
            if len(stripped_lines) < 3:
                log.warning(f"Degenerate output for {path}: only {len(stripped_lines)} lines")
                continue

            implementations[path] = code

        return implementations if implementations else None

    async def review(self, plan: EvolutionPlan,
                     implementations: dict[str, str]) -> tuple[bool, str]:
        """Council reviews the implementation."""
        code_summary = "\n\n".join(
            f"=== {path} ===\n{code[:3000]}"
            for path, code in implementations.items()
            if code is not None
        )

        approved, reason = await self.council.code_review(
            code=code_summary,
            intent=plan.reasoning,
        )

        return approved, reason

    async def execute(self, plan: EvolutionPlan, implementations: dict[str, str],
                      cycle: int) -> EvolutionResult:
        """Apply changes. Backup first, rollback on failure."""
        backups = {}
        affected = []

        try:
            # Backup existing files
            for path in implementations:
                full_path = self.base_path / path
                if full_path.exists():
                    backup_path = self.backup_dir / f"{path}.{cycle}.bak"
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(full_path, backup_path)
                    backups[path] = backup_path

            # Apply changes
            for path, content in implementations.items():
                full_path = self.base_path / path

                if content is None:
                    if full_path.exists():
                        full_path.unlink()
                        log.info(f"Deleted: {path}")
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content)
                    action = "Created" if path not in backups else "Modified"
                    log.info(f"{action}: {path}")

                affected.append(path)

            # Post-write syntax check
            for path, content in implementations.items():
                if content is not None and path.endswith(".py"):
                    full_path = self.base_path / path
                    try:
                        ast.parse(full_path.read_text())
                    except SyntaxError as e:
                        raise RuntimeError(
                            f"Post-write syntax error in {path}: {e}"
                        )

            await self.memory.store_procedural(
                strategy=f"{plan.action}: {plan.reasoning}",
                context=f"cycle_{cycle}",
                outcome=f"Applied to {len(affected)} files",
                success=True,
            )

            return EvolutionResult(
                success=True,
                action=plan.action,
                files_affected=affected,
                review_passed=True,
            )

        except Exception as e:
            log.error(f"Evolution failed: {e} — rolling back")
            self._rollback(backups)

            await self.memory.store_procedural(
                strategy=f"{plan.action}: {plan.reasoning}",
                context=f"cycle_{cycle}",
                outcome=f"Failed: {e}",
                success=False,
            )

            return EvolutionResult(
                success=False,
                action=plan.action,
                files_affected=affected,
                error=str(e),
                rollback_performed=True,
            )

    def _rollback(self, backups: dict[str, Path]):
        for path, backup_path in backups.items():
            full_path = self.base_path / path
            if backup_path.exists():
                shutil.copy2(backup_path, full_path)
                log.info(f"Rolled back: {path}")
