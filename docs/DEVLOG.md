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

## M2: network mode (2026-11-03)

**Before M2:** golden wire-format test fixed. Fixtures are now built inside the test and each case builds only its own envelope, so a broken data type fails those cases instead of crashing collection (`73ca348`). Step 0 (`ab9f64a`, pushed for M3): nicegui 3.19 and websockets 17.4 dependencies; `relay`/`node`/`sim` entry points; empty `net/`, `ui/node/`, `ui/sim/`, `ui/components/` packages with owners.

**Done** (every commit pushed to origin/main after ruff, format and pytest passed)
- `ui/components/editor.py` + `cursor.py`: the shared `RecordEditor` with CRDT-anchored cursor. Built first and pushed for Aijuka.
- `net/store.py`: append-only JSONL `ChangeStore`.
- `net/client.py`: `ReconnectingClient`:
  - `Hello(role)`;
  - heartbeat ping every 1 s, dead after 3 s without a pong, RTT from the echo;
  - try the next relay at once; capped exponential backoff after a full failed round;
  - no flapping back; manual offline.
- `net/ws_transport.py`: the frozen `Transport` on top of it.
- `net/relay.py`: `Relay`:
  - `CausalLog` reuse; answers `sync_req` itself and never forwards it;
  - first-sight forwarding (including changes learned through sync) to other nodes and each peer relay once;
  - JSONL log; peer links with sync on connect and every 2 s;
  - `pause()` hook; CLI.
- `net/node.py`: headless `Node` with sync on every (re)connect, JSONL replay, an anti-entropy timer, and a wall-clock `now`.
- `ui/node/`: the page (status bar, record list + New record dialog, editor, event log, Offline switch) and `uv run node`.
- Frozen code untouched: `git diff ab9f64a -- fieldsync/crdt fieldsync/runtime fieldsync/sim` is empty.

**Deviations from the approved plan**
- Commit order: client/WsTransport came before the relay, because the relay uses the client for peer links.
- One extra test (the relay triangle), found by mutation check 1.
- The node page has no automated UI test. The planned optional NiceGUI `User` smoke test needs pytest-asyncio, which we chose not to add, so the page was checked by real-process smoke tests instead (below).

**Tests added: 22** (95 → 117)
- cursor anchoring: 4;
- store: 5;
- backoff: 2;
- network: 11, all real WebSockets on 127.0.0.1 in one event loop:
  - convergence over one relay;
  - relay kill failover with outage edits;
  - heartbeat-timeout failover;
  - changes rescued from a dead relay;
  - relay restart serving a late joiner;
  - node restart keeping offline edits;
  - 2-relay and 3-relay no-loop;
  - no sync_req forwarding;
  - no flap-back;
  - all relays down, then back.
- Failover tests run nodes without periodic anti-entropy, so recovery must come from sync-on-reconnect.
- **Flakiness check:** `tests/net` passed 5 of 5 consecutive runs, both before and after the triangle test was added (about 4.7 s per run).

**Mutation checks** (each restored afterwards; suite green again)
1. *Broke:* the relay forwards duplicates too (the `duplicate` early return removed).
   - *Result at first: not caught.* All 17 network tests passed. With two relays, "never send back to the source" alone already stops a direct A↔B bounce.
   - *Fix:* added `test_no_forwarding_loop_in_relay_triangle`. Three fully peered relays form a cycle that only the duplicate rule breaks. Under the mutation it fails with 154 forwards against a bound of 44, and nothing stops circulating.
   - My first reading of this run was wrong: I thought the tests hung. In fact my grep looked for pytest's `=====` summary banner, which `-q` suppresses, so a fully green run printed nothing.
2. *Broke:* `Node` no longer syncs on (re)connect.
   - *Caught by:* `test_relay_kill_failover_keeps_outage_edits`, `test_changes_only_on_dead_relay_rescued_via_nodes`, `test_relay_restart_from_jsonl_serves_late_joiner`, `test_node_restart_keeps_offline_edits`, `test_all_relays_down_then_back`.
3. *Broke:* the relay forwards each `sync_req` to its other nodes.
   - *Caught by:* `test_relay_never_forwards_sync_requests`.
4. *Broke:* relay and node skip the JSONL replay on start.
   - *Caught by:* `test_relay_restart_from_jsonl_serves_late_joiner`, `test_node_restart_keeps_offline_edits`.

**Real-process smoke test** (2 relays + 2 `uv run node` UIs + a headless driver node, on spare ports)
- Pages served, titled `FieldSync · brian` / `FieldSync · aijuka`.
- The driver's record and edit reached both UI nodes and both relays (2 lines in each JSONL).
- After killing `relay_a`, both UI nodes logged `lost ws://localhost:18765, failing over`. The next edits reached both UI nodes through `relay_b` (4 lines each).
- No errors in any log; clean shutdown.

**Problems hit**
- Two test bugs, fixed before commit: a node edited a record before receiving it, and a predicate indexed an empty history.
- **Duplicate-ID hazard:** the smoke test restarted the driver node with the same ID and no data file, so it started again at seq 1. It only worked because its startup sync arrived before its first edit. This became the second half of the D27 operating rule: a node that lost its data file must take a new ID.

**Not verified by me:** interactive browser behaviour (typing, the cursor staying in place during remote edits, the New record dialog, the Offline switch). I have no browser automation available here. The page renders and data flows through the UI processes, but the README demo steps need a human run-through.

## M2 follow-up (2026-11-04)

**JSONL line endings:** `ChangeStore` opens its file with `newline="\n"`, so data files are LF on every OS. Added `test_file_uses_lf_line_endings_on_every_os`, which checks the written bytes contain no `\r`. It passes on Linux even without the fix; it guards the Windows CI job.

**Bug (owner's manual browser check): a remote delete before the cursor sends it to the end of the text**
- *Steps:* two nodes with the same record open. Node 2's cursor is inside a word; node 1 deletes text before that word. Node 2's cursor jumps to the end of the text.
- *Reproduced in a real browser first:* added Playwright (dev dependency) and `tests/e2e/test_editor_cursor.py`. It starts a relay and two `uv run node` processes on spare ports and drives two Chromium pages with the keyboard. Before the fix all three e2e tests failed:
  - delete before cursor: got `(19, 19)` (end of text), expected `(9, 9)`;
  - insert before cursor: got `(29, 29)`, expected `(19, 19)`. The insert case failed too in automation, although it looked fine by hand.
  - selection kept through a remote delete: got `(19, 19)`, expected `(6, 13)`.
- *Root cause 1, the browser hand-off (as the owner suspected; not `cursor.py` or the RGA):*
  - Remote changes are applied on the transport's reader task. `RecordEditor.refresh()` set the textarea value, then called `ui.run_javascript`, which needs a NiceGUI page context. On a background task it raised `RuntimeError: slot stack is empty`.
  - The value had already changed, so the browser put the caret at the end (its default), and the restore never ran.
  - The exception also propagated up through the replica's observer into `CausalLog.offer`. That skipped the observers after the editor (the page's event log) and stopped the causal-buffer drain for that offer.
  - It was logged in the node's console as `error handling node1#3 ... del 6`.
- *Fix 1:* send the JavaScript through the editor's own client (`self.textarea.client.run_javascript`). Both UI observers (editor and node page) now catch and log their own exceptions.
- *Root cause 2, found by running the e2e tests 10 times after fix 1 (the insert test failed 2/10):*
  - When node 1 types "NEW ", four remote changes arrive in quick succession.
  - `setSelectionRange` fires a `select` event, so each restore reported a position back to the server. The server re-anchored that position against its newer document, even though it was measured in the browser's older text, so the anchors drifted (to 17, 28, ...).
  - A restore could also run before the textarea showed the text it was computed for.
  - Instrumenting the page (a log of restore calls and textarea values) showed both.
- *Fix 2 (D28):*
  - Selection reports carry the browser's text and are ignored unless it equals the replica's text.
  - Restores carry their text. The browser applies only the newest one, once the textarea shows that text (retrying per animation frame, up to 60 frames).
- *Root cause 3, found under load:*
  - After fix 2, the full suite (and the e2e tests with 4 busy-loop CPU hogs) still failed the insert test occasionally: 1 in 5 full-suite runs, and once within 3 and once within 7 loaded runs.
  - Instrumentation (every textarea value assignment and `setSelectionRange`) caught it. The helper's `setSelectionRange(17)` queues a `select` event. The next remote value then arrives, and the browser moves the caret to the end (28). Only then does the `select` event fire, reporting 28.
  - At that moment the browser's text equals the replica's, so the text check accepted it, and the cursor was anchored to the end.
- *Fix 3:* track the selection only from user actions (`keyup`, `click`), not from `select`, which our own restores trigger.
- *After:*
  - e2e under the same CPU load: 0 failures in 15 runs;
  - full suite (125 tests including e2e): 5 of 5 green;
  - the delete case gives `(9, 9)`, and the insert case confirms nothing regressed.
- *Unit tests:* the cursor logic was never at fault, but the missing cases were added: remote delete before the cursor (anchor kept), remote delete after the cursor, a delete range spanning the cursor, and a non-empty selection surviving a remote delete plus insert.
- *e2e tests* are marked `e2e` and skip themselves when Playwright's Chromium isn't installed, so CI (and machines without browsers) are unaffected.
- *Test-suite interaction:* a session-scoped Playwright fixture kept its event loop alive and broke `asyncio.run` in the later `tests/net` tests (11 failures). The browser fixture is now module-scoped.
- *Known limits:*
  - The cursor anchor is updated only on keyup and click. A report taken while a remote edit is in flight is ignored, and the next restore uses the previous anchor, at most one keystroke stale.
  - A selection made only with the context menu ("Select all") isn't tracked until the next key or click.
  - If a remote value lands between a key's keydown and keyup, that keyup can report the browser's end-of-text caret. I haven't seen this in testing.

## M4 checkpoint 1: runtime hardening (2026-11-05)
- *Done (D29, owner-approved change to frozen internals):* `Replica._emit` isolates each observer call. An exception is logged (`fieldsync.runtime.replica`, with the observer and the event); the remaining observers and the delivery continue. The loop iterates over a copy of `observers`, so an observer that removes itself (a closing UI page) doesn't make the next one get skipped.
- *Tests:*
  - `test_failing_observer_does_not_stop_delivery_of_a_buffered_chain`: B has an always-raising observer plus a recording one. Six changes from A arrive newest first (all buffered), then the create releases the chain. B ends with A's text, history (same dots, same order) and clock, nothing pending, and the second observer saw every `apply`.
  - `test_crdt_errors_still_raise_with_observers_attached`: an exception from the RGA's `apply` still propagates out of `receive`.
- *Mutation check:* with the `try/except` removed, the first test fails with `RuntimeError: observer bug` raised out of `receive` when the create arrives. Restored afterwards.
- *Note:* `Node._persist_new` (the data-file writer) is also an observer. If writing fails it is now logged instead of raised; `_saved` isn't advanced, so the next event retries the write.

## M4 checkpoint 1b: loud data-file write failures (2026-11-05)
- *Why:* after D29 a failing data-file write (the node's persistence observer) was only logged by the replica. The owner asked for it to be loud.
- *Done:*
  - `Node._persist_new` catches `OSError` itself. It logs at ERROR (node ID, how many changes are unsaved, the error) and records `PersistError(message, since)`, where `since` is the first failure of the current run.
  - `Node.status` returns the transport's `ClientStatus` with `persist_error` filled in.
  - The node page shows a red "DATA FILE WRITE FAILING since …s: …" label in the header while it is set.
  - `_saved` doesn't advance on failure, so the next trace event retries from the first unsaved change. The error clears (logged at WARNING) once everything is written.
- *Test:* `tests/net/test_node_persist.py`. The store raises `OSError(ENOSPC)` on its first two appends. Creating a record fires two events (`create`, `send`), and both writes fail. The status shows the error with one `since`, the file is empty, and two ERROR lines are logged. The next edit writes both changes and the error clears. After stopping, the file loads back to exactly the node's history.
- *Mutation checks:*
  - Advancing `_saved` past a failed change (skip instead of retry): the test fails, because the next event finds nothing to write and the error clears too early.
  - Never clearing the error: the test fails at `seen[2] == ("edit", None)`.
  - Both were restored afterwards.
- *Known limit:* if the OS fails part-way through a line, the retry appends a full copy after the partial line. On reload that partial line sits mid-file, and `ChangeStore.load` treats it as corruption. A duplicate *full* line would be harmless, because the causal log ignores duplicates. Not fixed here: `ChangeStore` is shared with the relays.

## M3: simulator UI, visualisations, scenarios (2026-11-13)                                                                                             
                                                                                                                                                            
    **Done**                                                                                                                                                
    - `fieldsync/sim/scenarios.py`: 7 scripted scenarios (concurrent insert, insert + delete, out-of-order delivery, partition and heal, offline editing,   
    late joiner, message loss), each with `setup` / `recover` / `run` (D30).                                                                                
    - `fieldsync/ui/sim/spacetime.py`: SVG space-time diagram (D31).                                                                                        
    - `fieldsync/ui/sim/page.py` and `app.py`: `uv run sim` with side-by-side replica panes (shared `RecordEditor`), play/pause/step/settle, scenario       
    selector, latency/drop/reorder/hold controls, partition presets, manual deliver/drop for held messages, pending-buffer view, CRDT element list with     
    tombstones, trace and message logs, SVG download.                                                                                                       
    - Frozen paths untouched: `git diff <base-commit> -- fieldsync/crdt fieldsync/runtime fieldsync/net fieldsync/ui/node fieldsync/ui/components` is       
    `<empty? paste result>`. Existing `fieldsync/sim/*.py` files: `<unchanged? paste result>`.                                                              
                                                                                                                                                            
    **Tests added:** `<N>` (`tests/sim/test_scenarios.py` 15, `tests/sim/test_spacetime.py` 3, `tests/sim/test_sim_ui.py` 4 as written). Suite: `<before>`  
    -> `<after>` passed; ruff check and format clean: `<yes/no>`.                                                                                           
                                                                                                                                                            
    **Mutation checks** (to run, then restore; record the real result of each):                                                                             
    1. *Break:* in `_heal_all` remove `sim.heal()`. *Expect:* `test_every_scenario_diverges_then_converges` fails for `partition_and_heal`. *Result:*       
    `<paste>`.                                                                                                                                              
    2. *Break:* in `setup_out_of_order_delivery` deliver `first.id` instead of `second.id`. *Expect:*                                                       
    `test_out_of_order_delivery_buffers_until_dependency_arrives` fails (nothing buffered). *Result:* `<paste>`.                                            
    3. *Break:* in `spacetime.py` skip the `dropped` branch. *Expect:* `test_spacetime_svg_renders_dropped_held_and_buffered_states` fails. *Result:*       
    `<paste>`.                                                                                                                                              
                                                                                                                                                            
    **Not verified:** `<state honestly: e.g. "page.py was not exercised in a browser" or describe your manual run>`.                                        
                                                                                                                                                            
    **Deviations from the approved plan**                                                                                                                   
    - `<list any, e.g. changes you made while typing in the code, or "none">`.  