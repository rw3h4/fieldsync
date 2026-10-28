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

## M1: multi-record replica runtime (2026-10-28)

**Done**
- `ops.py` (additive): `CreateRecord(title, kind)` op; required `Change.record`; `record_id_for`; `check_replica_id`. A create is a change whose only op is `CreateRecord`, so it travels in the same causal stream as edits. `rga.py`, `clocks.py`, `ids.py`, `diff.py` are unchanged.
- `runtime/log.py`: `CausalLog` + `answer_sync`, extracted from `Replica` (commit 1 was a pure refactor; the 58 old tests passed untouched).
- `Replica`: one RGA per record, a G-Set registry (`RecordInfo`), per-record `edit`/`set_text`/`text`/`document`/`digest`, `state_digest`.
- Messages: `Hello(role)`, `Heartbeat(sent_at, pong)`.
- `TraceEvent.record` and the `create` kind.
- `Simulation`: `create_record`, per-record `edit`/`set_text`/`texts`, `record_ids`, record-aware `converged()`.
- Interfaces frozen in `docs/INTERFACES.md`, guarded by a golden wire-format test.

**Old tests: mechanical updates (owner-approved).** Each affected test now creates one shared record first. Wherever possible the record is synced *before* the test's own setup, so most expected values didn't move. Values that did shift, all because of the one extra create change:
- `test_local_edit_produces_one_change_per_edit`: `clock[A]` 1 → 2.
- `test_set_text_diffs_into_a_single_change`: `clock[A]` 2 → 3.
- `test_out_of_order_change_is_buffered_until_dependency_arrives`: `waiting_on` `{A: 1}` → `{A: 2}`.
- `test_duplicates_are_ignored`: `len(history)` 1 → 2.
- `test_messages_arrive_after_latency`: arrival time 1.0 → 2.0 (the edit is made at t=1.0, after the create arrives); message list skips the create (`messages()[1:]`).
- `test_full_loss_drops_everything`: message list skips the create (`messages()[1:]`).
- `test_trace_records_buffering`: trace kinds read from after the create's events (`trace[start:]`); expected lists unchanged.
- Changes that gained the field: `record=` added in `test_ops.py`, `test_convergence.py`, `test_transport.py`; `Hello()` → `Hello("node")`.
- Count: **58 → 58** after the update commit; **95** at the end of M1.

**Tests added (37):**
- `CausalLog` alone (6);
- `Hello`/`Heartbeat` (2);
- ops validation and JSON (11, including 6 parametrised bad replica IDs);
- golden wire format (7);
- replica multi-record (6);
- simulator multi-record (4);
- multi-record hypothesis property (1).

**Mutation checks** (each run on the finished code, then restored; the suite was green again afterwards):
1. *Broke:* `CausalLog.deliverable` returns True after the "next from origin" check (dependency check removed).
   - *Caught by:* `test_log::test_buffered_chain_is_released_in_causal_order`, `test_replica::test_transitive_dependency_on_third_replica`, `test_replica::test_edit_by_another_replica_waits_for_the_create` (`UnknownRecord: 'A-1'`), and both hypothesis property tests.
   - *Finding:* the plan predicted that `test_edit_before_record_exists_is_buffered` would catch this. It didn't: there the edit and the create have the same origin, so the seq check alone buffers the edit. I added the cross-origin test (B edits A's record and B's edit reaches C first), which does need the dependency check.
2. *Broke:* `record_id_for` returns `"rec-<seq>"` (origin dropped).
   - *Caught by:* collection of `test_transport.py` fails (the golden create change is rejected), `test_concurrent_offline_creates_get_distinct_ids_and_spread_everywhere` (`assert 'rec-1' != 'rec-1'`), and 5 more record tests including the multi-record property test.
   - *Note:* a plain `pytest` run stops at the collection error, so `--continue-on-collection-errors` was used to list every failing test.
3. *Broke:* `CausalLog.missing_for` leaves out record-creating changes.
   - *Caught by:* both late-joiner tests (`settle()` returns False: the joiner's edits wait forever for the creates), the concurrent-offline-create test, and the multi-record property test.

**Problems hit**
- Two first drafts of new tests were wrong and fixed before commit: a record-order assertion that hashed lists, and a muddled draft of the cross-origin test.
- `RGA.apply(op: Op)`: since `Op` now includes `CreateRecord`, the annotation is wider than what `RGA` handles. The replica never passes a `CreateRecord` to an RGA (creates go to the registry). `rga.py` was left frozen and unchanged; this is noted here instead.

**Deviations:** none from the approved plan, apart from the extra cross-origin test found by mutation check 1.
