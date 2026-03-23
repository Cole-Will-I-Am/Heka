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
            # 1. PERCEIVE
            log.info("[1/6] Perceiving...")
            state = perception.perceive()
            watchdog.heartbeat(cycle, "perceiving")

            # 2. THINK
            log.info("[2/6] Thinking...")
            thoughts = await consciousness.perceive_and_think(state)

            if cycle % INTROSPECTION_INTERVAL == 0:
                log.info("  Deep introspection...")
                own_source = perception.read_own_source()
                await consciousness.introspect(own_source)

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
            watchdog.heartbeat(cycle, "thinking")

            # 3. PLAN
            log.info("[3/6] Planning evolution...")
            plan = await evolution.plan(thoughts, state, cycle)

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
                implementations = await evolution.implement(plan)
                watchdog.heartbeat(cycle, "implementing")

                if implementations:
                    # 5. REVIEW
                    log.info("[5/6] Council review...")
                    approved, reason = await evolution.review(
                        plan, implementations
                    )

                    if approved:
                        log.info("  Council APPROVED")
                        # 6. EXECUTE
                        log.info("[6/6] Executing...")
                        result = await evolution.execute(
                            plan, implementations, cycle
                        )

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
                            await evolution.update_readme(result, plan)

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

            # Save state for resurrection
            watchdog.save_state({
                "cycle": cycle,
                "soul": soul.serialize(),
                "memory_stats": memory.stats(),
                "journal_stats": journal.stats(),
            })

            elapsed = time.time() - cycle_start
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
        "shutdown": "graceful",
    })

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
