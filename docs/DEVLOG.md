# Development log

## v1 Phase 1: scaffold and CRDT core (2026-10-17)
- **Done:** uv project, ruff, pytest + hypothesis. `editor/crdt/`: `ElementId`, `LamportClock`, `VectorClock`, `Insert`/`Delete`/`Change` with JSON round-trip, `RGA` (tombstones, higher-ID-first tie-break, idempotent apply, cursor anchoring helpers), single-range `diff`.
- **Tests:** unit tests plus a hypothesis property: random edits on 2–4 RGAs, applied in random causally valid orders, give identical text and element lists.
- **Mutation check:**
  - *Broke:* the tie-break loop in `RGA._integrate_insert` (`while pos < len(...) and self._elements[pos].id > op.id` → `... and False`), so a new insert never skips concurrent siblings with higher IDs.
  - *Caught by:* the hypothesis property test `tests/crdt/test_convergence.py`. It failed with the minimal divergence `AssertionError: {'ab', 'ba'}`: two replicas inserting one character each at the same spot ended in different orders.
  - *Restored:* the original `rga.py` was copied back; the full suite passed again (27 passed).
- **Commit:** `320ae60`.

## v1 Phase 2: replica runtime and simulator (2026-10-17)
- **Done:** `Transport` protocol and JSON message codec; `Replica` (causal buffer, op log in causal order, push-pull anti-entropy, trace events); deterministic scheduler; `SimNetwork` (latency, jitter, loss, reordering, partitions, offline, hold mode); `Simulation` facade.
- **Tests:** same seed → same trace; buffering then release; duplicates ignored; loss repaired by anti-entropy; partition and heal; hold mode; late joiner; hypothesis end-to-end (random edits + random faults → heal → converged).
- **Mutation checks** (both caught by the hypothesis end-to-end test `tests/sim/test_sim_properties.py`):
  1. *Broke anti-entropy:* in `Replica._answer_sync`, `missing = self.missing_for(req.clock)` → `missing = []`, so sync requests are answered with nothing. *Result:* `assert False` where `False = settle(max_time=300)`: after random faults the replicas never converged, because lost messages were never repaired.
  2. *Broke causal delivery:* `Replica._deliverable` made to return `True` immediately (no "next from origin" check, no dependency check). *Result:* `MissingDependency: unknown element 1@A`: a delete was applied before the insert it targets.
  - *Restored:* the original `replica.py` was copied back; the full suite passed again (58 passed).
- **Commit:** `bd9c229`.

## M0: setup and re-baseline (2026-10-28)

**Why:** The course assignment changed to "design and implement a fault-tolerant distributed service" where multiple clients submit, retrieve and update records through networked processes. Our architecture review was approved, and PROJECT_BRIEF.md v2 replaces v1. The project is now **FieldSync** (community health NGO, offline field officers). Network mode with two relays and failover becomes core; the simulator stays, to illustrate. See brief v2 §6 for what changes.

**Audit:**
- v1 Phases 1–2 done and committed. Phases 3–7 (simulator UI, visualisations, scenarios, network mode, docs) not started.
- Baseline before the rename: **58 passed**, ruff check and format clean (Python 3.12.14).

**Problem hit: the renamed folder had no git history.** `~/Projects/fieldsync` was a copy of the old project without its hidden files (`.git/`, `.gitignore`, `.python-version`). Without `.python-version`, the first `uv sync` built the env on Python 3.14.6 and rewrote `uv.lock` (the tests still passed: 58). **Fix:** copied `.git/` from the original folder (`~/Ndejje/Y4/Distributed Systems/collaborative-text-editor`, left untouched as a backup), restored the three files from git, rebuilt `.venv` on 3.12.

**Rename (mechanical, imports only):**
- `editor/crdt` → `fieldsync/crdt`. `clocks.py`, `ids.py`, `diff.py` are byte-identical; `ops.py` and `rga.py` changed only in their import lines.
- `editor/sim/{replica,transport,trace}` → `fieldsync/runtime/`; `editor/sim/{network,scheduler,simulation}` → `fieldsync/sim/`.
- `tests/sim/{test_replica,test_transport,conftest}` → `tests/runtime/`.
- All moves with `git mv`; `git log --follow` keeps Phase 1–2 history.
- Two import blocks re-sorted by `ruff --fix` (the new module paths sort differently).
- **After:** **58 passed**, ruff clean.

**Also in M0:** pyproject renamed (`fieldsync`, `>=3.12`, ruff `py312`, no new lint findings); `.gitignore` extended (relay/node JSON, logs, `results/raw/`); GitHub Actions CI (ruff check, format check, pytest); CONTRIBUTING.md; this log and DECISIONS.md; assignment PDF moved to `docs/assignment.pdf`; remote `origin` = `git@github.com:rw3h4/fieldsync.git`, branch `main`.

**Deviations:** none from the approved plan. The `.git` recovery was added to the plan before approval.

**Mutation checks:** none. M0 changes no logic.
