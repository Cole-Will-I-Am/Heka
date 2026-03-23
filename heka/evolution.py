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
import asyncio
import json
import logging
import os
import shutil
import subprocess
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

    def __init__(self, base_path: Path | str, architect: Mind, coder: Mind,
                 council: Council, memory: Memory):
        self.base_path = Path(base_path)
        self.architect = architect
        self.coder = coder
        self.council = council
        self.memory = memory
        self.backup_dir = self.base_path / ".heka" / "backups"
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

        plan_data = await self.architect.generate_json(prompt, num_predict=1200)

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
        implementations: dict[str, Optional[str]] = {}

        def _strip_fences(text: str) -> str:
            code = text.strip()
            if code.startswith("```"):
                lines = code.split("\n")
                end = len(lines)
                for i in range(len(lines) - 1, 0, -1):
                    if lines[i].strip().startswith("```"):
                        end = i
                        break
                code = "\n".join(lines[1:end])
            return code

        async def _implement_one(file_spec: dict) -> tuple[Optional[str], Optional[str]]:
            path = file_spec.get("path", "")
            intent = file_spec.get("intent", "")
            if not path or not intent:
                return None, None

            full_path = self.base_path / path
            existing = full_path.read_text(errors="replace") if full_path.exists() else ""

            if plan.action == "delete":
                return path, None

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

            thought = await self.coder.think(prompt, num_predict=4096)
            code = _strip_fences(thought.content)

            if path.endswith(".py"):
                try:
                    ast.parse(code)
                except SyntaxError as e:
                    log.warning(f"Coder produced invalid Python for {path}: {e}")
                    retry = await self.coder.think(
                        f"Your previous code had a syntax error: {e}\n\n"
                        "Fix it. Output ONLY the corrected complete file.",
                        context=code,
                        num_predict=4096,
                    )
                    code = _strip_fences(retry.content)
                    try:
                        ast.parse(code)
                    except SyntaxError:
                        log.error(f"Retry also produced invalid Python for {path}")
                        return None, None

            stripped_lines = [l for l in code.split("\n") if l.strip()]
            if len(stripped_lines) < 3:
                log.warning(f"Degenerate output for {path}: only {len(stripped_lines)} lines")
                return None, None

            return path, code

        tasks = [_implement_one(file_spec) for file_spec in plan.files]
        for path, code in await asyncio.gather(*tasks):
            if path:
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
                    try:
                        ast.parse(content)
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

    async def update_readme(self, result: EvolutionResult, plan: EvolutionPlan,
                            codebase_snapshot: Optional[dict] = None):
        """Have the Architect update README.md to reflect what changed."""
        readme_path = self.base_path / "README.md"
        if not readme_path.exists():
            return

        current_readme = readme_path.read_text(errors="replace")

        file_list = []
        if codebase_snapshot and isinstance(codebase_snapshot.get("files"), list):
            for item in codebase_snapshot["files"]:
                path = item.get("path")
                if not path:
                    continue
                loc = item.get("loc")
                if isinstance(loc, int):
                    file_list.append(f"{path} ({loc} lines)")
                else:
                    file_list.append(str(path))
        else:
            py_files = sorted(self.base_path.rglob("*.py"))
            py_files = [
                f for f in py_files
                if not any(
                    p.startswith(".") or p == "__pycache__"
                    for p in f.relative_to(self.base_path).parts
                )
            ]
            for f in py_files:
                try:
                    loc = len([
                        l for l in f.read_text(errors="replace").split("\n")
                        if l.strip() and not l.strip().startswith("#")
                    ])
                    file_list.append(f"{f.relative_to(self.base_path)} ({loc} lines)")
                except Exception:
                    file_list.append(str(f.relative_to(self.base_path)))

        prompt = f"""The project just evolved. Update the README.md to reflect the current state.

WHAT CHANGED THIS CYCLE:
  Action: {plan.action}
  Files affected: {result.files_affected}
  Reasoning: {plan.reasoning}

CURRENT PROJECT FILES:
{chr(10).join(f'  - {f}' for f in file_list)}

CURRENT README:
{current_readme}

Rules:
- Keep the existing structure and tone
- Update the Project Structure section to match current files and line counts
- Update the total line count
- If new capabilities were added, mention them briefly
- Do NOT remove existing sections
- Do NOT add fluff — keep it tight
- Output ONLY the complete updated README.md content"""

        thought = await self.architect.think(prompt, temperature=0.3, num_predict=2400)
        new_readme = thought.content.strip()

        # Strip markdown fences if the model wrapped it
        if new_readme.startswith("```"):
            lines = new_readme.split("\n")
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip().startswith("```"):
                    end = i
                    break
            new_readme = "\n".join(lines[1:end])

        # Sanity check — must still look like a README
        if len(new_readme) < 200 or "# Heka" not in new_readme:
            log.warning("Architect produced invalid README update — skipping")
            return

        readme_path.write_text(new_readme)
        log.info("README.md updated by Architect")

    async def git_record(self, cycle: int, plan: EvolutionPlan,
                         result: EvolutionResult) -> dict:
        if not result.success:
            return {"committed": False, "pushed": False, "reason": "evolution_failed"}
        return await asyncio.to_thread(self._git_record_sync, cycle, plan, result)

    def _git_record_sync(self, cycle: int, plan: EvolutionPlan,
                         result: EvolutionResult) -> dict:
        repo = self.base_path
        git_dir = repo / ".git"
        if not git_dir.exists():
            return {"committed": False, "pushed": False, "reason": "no_git_repo"}

        def _run(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        files_to_stage = []
        for path in result.files_affected:
            full = repo / path
            if full.exists() or path in result.files_affected:
                files_to_stage.append(path)
        if (repo / "README.md").exists():
            files_to_stage.append("README.md")

        if files_to_stage:
            add = _run(["add", "--", *files_to_stage])
            if add.returncode != 0:
                return {
                    "committed": False,
                    "pushed": False,
                    "reason": f"git_add_failed: {add.stderr.strip()[:200]}",
                }

        diff = _run(["diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return {"committed": False, "pushed": False, "reason": "no_staged_changes"}

        short_reason = " ".join(plan.reasoning.split())[:72] or "autonomous update"
        message = f"heka cycle {cycle}: {plan.action} - {short_reason}"
        commit = _run(["commit", "-m", message], timeout=30)
        if commit.returncode != 0:
            return {
                "committed": False,
                "pushed": False,
                "reason": f"git_commit_failed: {commit.stderr.strip()[:200]}",
            }

        pushed = False
        push_reason = "push_disabled"
        if os.environ.get("HEKA_GIT_PUSH", "1").lower() not in {"0", "false", "no"}:
            branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
            if branch.returncode == 0:
                branch_name = branch.stdout.strip() or "main"
                push = _run(["push", "origin", branch_name], timeout=45)
                if push.returncode == 0:
                    pushed = True
                    push_reason = "ok"
                else:
                    push_reason = f"git_push_failed: {push.stderr.strip()[:200]}"
            else:
                push_reason = "branch_unknown"

        return {"committed": True, "pushed": pushed, "reason": push_reason}

    def _rollback(self, backups: dict[str, Path]):
        for path, backup_path in backups.items():
            full_path = self.base_path / path
            if backup_path.exists():
                shutil.copy2(backup_path, full_path)
                log.info(f"Rolled back: {path}")
