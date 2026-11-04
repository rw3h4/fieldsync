# Contributing to FieldSync

## 1. Install uv

FieldSync uses [uv](https://docs.astral.sh/uv/) for Python, dependencies and commands.

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

You don't need to install Python yourself: uv downloads the version pinned in
`.python-version` (3.12).

## 2. Clone and install

```bash
git clone git@github.com:rw3h4/fieldsync.git
cd fieldsync
uv sync            # creates .venv with Python 3.12 and the dev tools
```

## 3. Run the checks

These three must pass before every commit (CI runs the same on every push and PR):

```bash
uv run ruff check .
uv run ruff format --check .     # `uv run ruff format .` fixes formatting
uv run pytest
```

Browser end-to-end tests (`tests/e2e`, marked `e2e`) are skipped unless Chromium is installed: run `uv run playwright install chromium` once, then `uv run pytest -m e2e`.

## 4. Run the app

Network mode is runnable now (see README, "Run network mode"); the rest arrive with their
milestones:

| Command | What | Milestone |
|---|---|---|
| `uv run relay --id relay_a --port 8765 ...` | Relay server | M2 (done) |
| `uv run node --id brian --port 8081 ...` | Editor node (serves a page on localhost) | M2 (done) |
| `uv run sim` | Simulator UI | M3 |
| `uv run loadgen` | Load generator | M4 |

Until then, the simulator can be driven from Python:

```bash
uv run python -c "
from fieldsync.sim import Simulation
sim = Simulation(['A', 'B'])
rec = sim.create_record('A', 'Kasese visit', 'visit_report')
sim.settle()
sim.edit('A', rec, 0, insert='hello')
sim.edit('B', rec, 0, insert='world ')
sim.settle()
print(sim.record_ids(), sim.texts(rec), 'converged:', sim.converged())
"
```

## 5. Layout

```
fieldsync/crdt/      pure CRDT logic: ids, clocks, ops, rga, diff (frozen; owner approval to change)
fieldsync/runtime/   causal log, replica (records + RGAs), messages + Transport, trace
fieldsync/sim/       deterministic scheduler, simulated network, Simulation facade
fieldsync/net/       relay, reconnecting client, WsTransport, headless Node, JSONL store
fieldsync/ui/        NiceGUI: node page, shared components (RecordEditor), sim UI (M3)
tests/               mirrors the package layout
docs/                INTERFACES.md (frozen API), DEVLOG.md, DECISIONS.md, assignment.pdf
```

## 6. Working rules

- Read `PROJECT_BRIEF.md` and `CLAUDE.md`. Interfaces frozen at the end of M1
  (messages, `Transport`, replica runtime API) change only with owner approval.
- Work on a branch, keep commits small, and prefix messages with the milestone:
  `M2: relay failover on heartbeat timeout`.
- Never add a library that implements CRDT / conflict-resolution logic.
- Everything must run on one laptop over localhost.
- Record design decisions in `docs/DECISIONS.md` and progress in `docs/DEVLOG.md`.
