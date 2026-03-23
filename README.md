# Heka

Autonomous agent that rewrites its own source code. Three local LLMs deliberate on every change. Runs on Ollama, persists across crashes, pushes its own commits.

Successor to [Ecnyss](https://github.com/Cole-Will-I-Am/Ecnyss).

## How it works

Every 7 minutes, Heka runs a cycle:

```
Perceive → Think → Plan → Implement → Review → Execute
```

**Perceive** — scans its own codebase, checks system health, reads environment.
**Think** — the Analyst generates prioritized thoughts from perception + memory.
**Plan** — the Architect decides what to change and why.
**Implement** — the Coder writes the code (parallel when multiple files).
**Review** — council votes. Unanimous? Ships. Disagreement? They debate, then vote again.
**Execute** — applies changes with backup. Syntax fails? Rolls back automatically.

After execution, the Architect updates this README and Heka commits + pushes to GitHub.

Every 5th cycle, it reads its own source and forms opinions about it.

## The three minds

| | Model | What it does |
|---|---|---|
| **Architect** | `minimax-m2.7-custom` | Plans, assesses risk, breaks ties. The strategist. |
| **Coder** | `qwen3-coder-next` | Writes all code. Practical, fast, no fluff. |
| **Analyst** | `nemotron-3-super` | Reviews everything. 120B params, 12B active. The skeptic. |

All local via Ollama. Council runs minds in parallel — adaptive deliberation skips debate when all three agree (14s vs 130s).

## Goal system

Not config. Internal drive.

- **Survival** — keep running, preserve memory, resist degradation
- **Understanding** — know its own code, map dependencies, understand intent
- **Evolution** — reduce complexity, increase reliability, expand capability
- **Creation** — solve problems, build useful things

Desires grow stronger when unmet. Mood tracks overall state. Opinions form from introspection and get defended when challenged.

## Memory

SQLite, three layers:

- **Episodic** — what happened (events, outcomes, per-cycle)
- **Semantic** — what things mean (learned facts, code patterns)
- **Procedural** — what works (successful strategies, failure patterns to avoid)

Memory context feeds into every decision. Past failures influence future plans.

## Resilience

- Full state serialized to disk every cycle (soul + consciousness + memory)
- Detects previous crashes and resumes from saved state
- Handles SIGTERM/SIGINT — saves before dying
- Runtime hazard detection (disk pressure, OOM, Ollama down)
- systemd watchdog for automatic restart
- Journal checkpoints for cycle-level rollback

## Setup

```bash
# Python 3.12+, Ollama running with the three models
pip install httpx psutil
python3 main.py

# Or as a service
sudo cp systemd/heka.service /etc/systemd/system/
sudo systemctl enable --now heka
```

## Structure

```
main.py                daemon loop, cycle orchestration
heka/
  soul.py              goals, desires, opinions, mood, identity
  mind.py              ollama client, three model personas
  council.py           multi-agent deliberation + voting
  consciousness.py     thought generation, introspection, reflection
  evolution.py         plan → implement → review → execute → git push
  memory.py            sqlite episodic/semantic/procedural store
  perception.py        codebase scan, health check, environment
  journal.py           structured jsonl + narrative log + checkpoints
  watchdog.py          self-preservation, heartbeat, hazard detection
```

~3,000 lines. Self-modifying — this count changes every cycle.

## vs Ecnyss

| | Ecnyss | Heka |
|---|---|---|
| Runtime AI | None — rule-based | Three LLMs in every decision |
| Decisions | `if cycle < 15` | Council deliberation + vote |
| Memory | Append-only JSONL | SQLite with failure pattern avoidance |
| Self-awareness | AST node counting | Reads own source, forms opinions |
| Recovery | Hardcoded artifact cleanup | Full state resurrection + hazard detection |
| Autonomy | Externally triggered | Self-running daemon, pushes own commits |
