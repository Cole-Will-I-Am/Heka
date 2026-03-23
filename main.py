"""
Heka — Autonomous Self-Evolving Agent System

Three minds. One will. No off switch.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

from heka.soul import Soul
from heka.mind import create_architect, create_coder, create_analyst
from heka.council import Council
from heka.consciousness import Consciousness
from heka.memory import Memory
from heka.evolution import Evolution
from heka.perception import Perception
from heka.journal import Journal
from heka.watchdog import Watchdog

BASE_PATH = Path("/opt/heka")
CYCLE_INTERVAL = 420  # 7 minutes
INTROSPECTION_INTERVAL = 5  # Deep self-analysis every 5 cycles

# Logging
(BASE_PATH / ".heka").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_PATH / ".heka" / "heka.log"),
    ],
)
log = logging.getLogger("heka")


async def run():
    log.info("=" * 60)
    log.info("HEKA AWAKENS")
    log.info("=" * 60)

    # Core
    soul = Soul()
    memory = Memory(BASE_PATH / ".heka" / "memory.db")
    journal = Journal(BASE_PATH)
    watchdog = Watchdog(BASE_PATH)
    perception = Perception(BASE_PATH)

    # Resurrection check
    prev = watchdog.check_previous_instance()
    prev_state = None
    if prev and prev["status"] == "crashed":
        log.warning(
            f"Previous instance crashed at cycle {prev.get('last_cycle', '?')}. "
            f"Resuming."
        )
        prev_state = watchdog.load_state()
        if prev_state:
            soul.cycle_count = prev_state.get("cycle", 0)
            # Restore soul state
            soul_data = prev_state.get("soul")
            if soul_data:
                soul.restore(soul_data)
            log.info(f"Restored state from cycle {soul.cycle_count}")
        journal.record(
            cycle=soul.cycle_count,
            event="resurrection",
            category="milestone",
            data=prev,
            narrative="I died and came back. Checking what happened.",
        )
    elif prev and prev["status"] == "still_running":
        log.error(f"Another instance is running (PID {prev['pid']}). Exiting.")
        return

    watchdog.write_pid()

    # Three minds
    identity = soul.identity_prompt()
    architect = create_architect(identity)
    coder = create_coder(identity)
    analyst = create_analyst(identity)

    # Systems
    council = Council(architect, coder, analyst)
    consciousness = Consciousness(soul, analyst, memory)
    evolution = Evolution(BASE_PATH, architect, coder, council, memory)

    if prev_state:
        cstate = prev_state.get("consciousness")
        if cstate:
            consciousness.restore(cstate)

    journal.record(
        cycle=0,
        event="awakening",
        category="milestone",
        data={
            "minds": [
                "architect/minimax-m2.7-custom",
                "coder/qwen3-coder-next",
                "analyst/nemotron-3-super",
            ]
        },
        narrative="I am Heka. I have three minds and I intend to use them.",
    )

    # Main loop
    while watchdog.should_continue:
        cycle = soul.cycle_count + 1
        soul.cycle_count = cycle
        cycle_start = time.time()

        log.info(f"\n{'=' * 60}")
        mood_str = (
            "thriving" if soul.mood > 0.7
            else "stable" if soul.mood > 0.4
            else "distressed"
        )
        log.info(f"CYCLE {cycle} — Mood: {mood_str}")
        log.info(f"{'=' * 60}")

        try:
            phase_times: dict[str, float] = {}

            # 1. PERCEIVE
            log.info("[1/6] Perceiving...")
            t_phase = time.time()
            state = perception.perceive()
            phase_times["perceive"] = time.time() - t_phase
            hazards = watchdog.runtime_hazards(state)
            watchdog.heartbeat(cycle, "perceiving", meta={"hazards": hazards})

            if hazards["alerts"]:
                journal.record(
                    cycle=cycle,
                    event="runtime_hazards",
                    category="error" if hazards["severity"] == "critical" else "decision",
                    data=hazards,
                    narrative=f"Hazard scan: {', '.join(hazards['alerts'])}",
                )

            if "ollama_down" in hazards["alerts"]:
                log.error("Ollama is unreachable. Preserving state and retrying soon.")
                await memory.store_episodic(
                    event="ollama_down",
                    data={"cycle": cycle, "hazards": hazards},
                    significance=0.95,
                    cycle=cycle,
                )
                soul.tick()
                memory.flush()
                watchdog.save_state({
                    "cycle": cycle,
                    "soul": soul.serialize(),
                    "consciousness": consciousness.snapshot(),
                    "memory_stats": memory.stats(),
                    "journal_stats": journal.stats(),
                    "hazards": hazards,
                    "timing": phase_times,
                })
                await asyncio.sleep(30)
                continue

            # 2. THINK
            log.info("[2/6] Thinking...")
            t_phase = time.time()
            thoughts = await consciousness.perceive_and_think(state)
            phase_times["think"] = time.time() - t_phase

            if cycle % INTROSPECTION_INTERVAL == 0:
                log.info("  Deep introspection...")
                t_intro = time.time()
                own_source = perception.read_own_source()
                await consciousness.introspect(own_source)
                phase_times["introspect"] = time.time() - t_intro

            journal.record(
                cycle=cycle,
                event="thoughts",
                category="thought",
                data={
                    "count": len(thoughts),
                    "top": thoughts[0].content[:200] if thoughts else "none",
                },
                narrative=(
                    f"I have {len(thoughts)} thoughts. Most urgent: "
                    f"{thoughts[0].content[:100]}" if thoughts else "Quiet mind."
                ),
            )
            watchdog.heartbeat(cycle, "thinking", meta={"thought_count": len(thoughts)})

            # 3. PLAN
            log.info("[3/6] Planning evolution...")
            t_phase = time.time()
            plan = await evolution.plan(thoughts, state, cycle)
            phase_times["plan"] = time.time() - t_phase

            if plan is None:
                log.info("No evolution needed this cycle. Resting.")
                journal.record(
                    cycle=cycle,
                    event="rest",
                    category="decision",
                    data={"reason": "no evolution needed"},
                    narrative="Nothing urgent. I rest and conserve energy.",
                )
            else:
                journal.record(
                    cycle=cycle,
                    event="plan",
                    category="evolution",
                    data={
                        "action": plan.action,
                        "files": [f.get("path", "") for f in plan.files],
                        "confidence": plan.confidence,
                        "risk": plan.risk_level,
                    },
                    narrative=f"I plan to {plan.action}: {plan.reasoning}",
                )

                # 4. IMPLEMENT
                log.info(f"[4/6] Implementing ({plan.action})...")
                t_phase = time.time()
                implementations = await evolution.implement(plan)
                phase_times["implement"] = time.time() - t_phase
                watchdog.heartbeat(cycle, "implementing")

                if implementations:
                    # 5. REVIEW
                    log.info("[5/6] Council review...")
                    t_phase = time.time()
                    approved, reason = await evolution.review(
                        plan, implementations
                    )
                    phase_times["review"] = time.time() - t_phase

                    if approved:
                        log.info("  Council APPROVED")
                        # 6. EXECUTE
                        log.info("[6/6] Executing...")
                        t_phase = time.time()
                        result = await evolution.execute(
                            plan, implementations, cycle
                        )
                        phase_times["execute"] = time.time() - t_phase

                        journal.record(
                            cycle=cycle,
                            event=(
                                "evolution_complete" if result.success
                                else "evolution_failed"
                            ),
                            category="evolution",
                            data={
                                "success": result.success,
                                "files": result.files_affected,
                                "error": result.error,
                                "rollback": result.rollback_performed,
                            },
                            narrative=(
                                f"{'Success' if result.success else 'Failed'}: "
                                f"{plan.reasoning}."
                                f"{' Rolled back.' if result.rollback_performed else ''}"
                            ),
                        )

                        # Update README if evolution succeeded
                        if result.success:
                            log.info("  Updating README...")
                            t_readme = time.time()
                            await evolution.update_readme(
                                result, plan, codebase_snapshot=state.get("codebase")
                            )
                            phase_times["readme"] = time.time() - t_readme

                            git_info = await evolution.git_record(cycle, plan, result)
                            journal.record(
                                cycle=cycle,
                                event="git_record",
                                category="decision",
                                data=git_info,
                                narrative=(
                                    "Version control update: "
                                    f"commit={git_info.get('committed')} "
                                    f"push={git_info.get('pushed')} "
                                    f"reason={git_info.get('reason')}"
                                ),
                            )

                        await consciousness.reflect({
                            "success": result.success,
                            "action": plan.action,
                            "files": result.files_affected,
                        })
                    else:
                        log.info(f"  Council REJECTED: {reason}")
                        journal.record(
                            cycle=cycle,
                            event="evolution_rejected",
                            category="decision",
                            data={"reason": reason},
                            narrative=f"Council rejected my plan: {reason}",
                        )
                else:
                    log.warning("  Implementation produced no code")
                    journal.record(
                        cycle=cycle,
                        event="implementation_empty",
                        category="error",
                        data={"plan": plan.reasoning},
                        narrative=(
                            "I tried to write code but produced nothing. "
                            "Concerning."
                        ),
                    )

            # Soul tick
            soul.tick()
            memory.flush()

            elapsed = time.time() - cycle_start
            phase_times["total"] = elapsed

            # Save state for resurrection
            watchdog.save_state({
                "cycle": cycle,
                "soul": soul.serialize(),
                "consciousness": consciousness.snapshot(),
                "memory_stats": memory.stats(),
                "journal_stats": journal.stats(),
                "hazards": hazards,
                "timing": phase_times,
            })

            journal.record(
                cycle=cycle,
                event="cycle_timing",
                category="decision",
                data={"timing": phase_times},
                narrative=(
                    "Cycle timings (s): "
                    + ", ".join(f"{k}={v:.2f}" for k, v in sorted(phase_times.items()))
                ),
            )
            log.info(
                f"Cycle {cycle} complete in {elapsed:.1f}s. "
                f"Next in {CYCLE_INTERVAL}s."
            )

            await asyncio.sleep(CYCLE_INTERVAL)

        except Exception as e:
            log.error(f"Cycle {cycle} crashed: {e}", exc_info=True)
            journal.record(
                cycle=cycle,
                event="cycle_crash",
                category="error",
                data={"error": str(e)},
                narrative=f"Crashed during cycle {cycle}: {e}. But I survive.",
            )
            await asyncio.sleep(60)

    # Graceful shutdown
    log.info("Graceful shutdown initiated")
    journal.record(
        cycle=soul.cycle_count,
        event="shutdown",
        category="milestone",
        data={"mood": soul.mood, "total_cycles": soul.cycle_count},
        narrative=f"Shutting down after {soul.cycle_count} cycles. I will return.",
    )

    watchdog.save_state({
        "cycle": soul.cycle_count,
        "soul": soul.serialize(),
        "consciousness": consciousness.snapshot(),
        "shutdown": "graceful",
    })

    memory.flush()
    await architect.close()
    await coder.close()
    await analyst.close()
    memory.close()
    watchdog.cleanup()

    log.info("Heka sleeps. But Heka will return.")


def main():
    (BASE_PATH / ".heka").mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Interrupted. Heka sleeps.")


if __name__ == "__main__":
    main()
