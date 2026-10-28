# FieldSync: Project Brief v2 (approved architecture)

> This replaces Project Brief v1. v1 described a "design and illustrate" project centred on a simulator.
> The course assignment was then updated, and our architecture review was **approved by the professor**.
> FieldSync is now a **fault-tolerant distributed service that runs as networked processes**. The simulator remains, but its job is to illustrate the system, not to be the system.

## 1. The assignment (verbatim requirements; these are what we are graded on)

Course: BSE 4103 / BCS 3107 Distributed Systems. Group of two: **TUMWESIGYE BRIAN (23/2/370/W/552)** and **ARINAITWE AIJUKA (23/2/370/W/143)**.

Task: *Design and implement a fault-tolerant distributed service.* Build a small distributed application for a real-world organisation. The service must let **multiple clients submit, retrieve, and update records through networked processes**. It must **keep providing useful service when a server process fails, or when the network between components is slow or temporarily unavailable**. It must be a **working prototype** that demonstrates distributed-system principles.

The prototype and report must cover:

- **(a) Requirements and architecture:** users, distributed processes, hardware/software components, communication links, at least three functional requirements, and a labelled architecture diagram.
- **(b) Implementation:** a working prototype with at least two communicating processes or clients. Demonstrate the communication mechanism and show how data is exchanged.
- **(c) Coordination and consistency:** show how concurrent requests and shared data are handled. Implement or clearly simulate a synchronisation strategy, and explain how conflicting updates are prevented or resolved.
- **(d) Fault handling and reliability:** demonstrate at least one failure scenario (server restart, dropped connection, timeout, unavailable service). Show the recovery behaviour and explain the limits.
- **(e) Performance and scalability evaluation:** at least two test cases with different numbers of requests or clients. Report measurable results (response time, throughput, successful requests, failure recovery time) and interpret them.
- **(f) Demonstration and report:** source code, setup/run instructions, test evidence (screenshots, logs, tables), a 5–8 page report, and a video of at most 15 minutes showing **each member's contribution**.

Constraints: must run on a **standard laptop**. Duration is three weeks.

## 2. Context and requirements (approved)

**Context:** a community health NGO with an HQ in Kampala and field offices in rural districts. Field staff and HQ staff share records (visit reports, case notes). Field connectivity is unreliable.

**Users:** field officer (writes records, often offline), HQ supervisor (reviews and edits), system admin (runs relays, watches sync status).

**Functional requirements:**
- **FR1 Submit:** create a new record (title + type: visit report or case note).
- **FR2 Retrieve:** list all records and open any of them.
- **FR3 Update:** edit a record's text with other users at the same time.
- **FR4 Offline:** keep creating and editing while disconnected; merge on reconnect.
- **FR5 Status:** each client shows its connection (relay A / relay B / offline), sync state and pending changes.

**Non-functional:** stays available when a relay fails; all replicas converge; sync under 1 s on a LAN; runs on standard laptops.

## 3. Approved architecture

```
 Field officer laptop A ─┐                    ┌─ Relay A (primary)
 Field officer laptop B ─┼── WebSocket/JSON ──┤        ⇅ relay-to-relay sync
 HQ supervisor laptop  ──┘   (failover to B)  └─ Relay B (standby)
```

- **Editor node** (one per user, `uv run node`): a Python process holding a **full replica of every record**. It serves a NiceGUI page on localhost; the browser is only the screen.
- **Relay servers A and B** (`uv run relay`): forward changes between nodes, keep a **durable op log** (JSON file) and the record registry, and **sync with each other** using the same anti-entropy protocol as nodes. Relays **never order or merge** edits. Convergence comes only from the CRDT inside each node.
- **Failover:** each node knows an ordered relay list `[A, B]`. Heartbeat timeouts detect a dead relay; the node switches to the next one and immediately sends a sync request to fill gaps.
- **Simulator** (`uv run sim`): runs 2–4 replicas in one process over a controllable fake network. It shares the same replica runtime code. It illustrates concurrency and failures for the demo and report.
- **Load generator** (`uv run loadgen`): headless nodes that make scripted edits and record timings, for task (e).

### Layers inside a node (keep these boundaries; the code layout should match them)
1. **UI** (NiceGUI): displays state only, never decides conflicts.
2. **Replica runtime** (`fieldsync/runtime/`): causal buffer, op log, anti-entropy, messages, the `Transport` interface.
3. **CRDT core** (`fieldsync/crdt/`): RGA, Lamport and vector clocks. Pure logic, no I/O.
4. **Transport**: `WsTransport` (real network) or `SimTransport` (simulator), behind the `Transport` interface.

## 3b. Naming and package layout

The project, repo, folder and Python package are all named **`fieldsync`**. Target layout:

```
fieldsync/
  crdt/      ids, clocks, ops, rga, diff            (pure logic)
  runtime/   replica, transport (messages + Transport), trace   (shared by sim and net)
  sim/       scheduler, network (SimNetwork, SimTransport), simulation, scenarios
  net/       relay, ws_transport, node               (M2)
  ui/        NiceGUI pages and visualisations        (M2, M3)
  eval/      load generator and evaluation scripts   (M4)
tests/  docs/  results/
```

Today the package is `editor/` and the replica runtime sits inside `editor/sim/`. M0 renames and moves the code to the layout above. This is a mechanical change (imports only, no logic), checked by the existing tests.

## 4. Consistency design (approved; already partly built)

- **RGA sequence CRDT** for each record's text. Each character has a unique **Lamport ID** `(counter, replica)`. Inserts reference the ID they follow. Concurrent inserts at the same spot are ordered **higher ID first**. Deletes leave **tombstones**.
- **Vector clocks** on every `Change` (one change = one user edit). They are used for **causal delivery** (early changes wait in a pending buffer) and **anti-entropy** (exchange clocks, send what the other side is missing).
- **Guarantee:** strong eventual consistency.
- **Synchronisation strategy for task (c):** optimistic, lock-free replication with causal delivery. Locking was considered and rejected because it blocks offline users and makes the lock server a single point of failure. This rationale must appear in the docs.
- **Records themselves are replicated too.** Creating a record is a replicated operation. The record set is a grow-only set (G-Set CRDT): records are never deleted in this prototype. Record IDs are generated without coordination (for example `"<replica>-<counter>"`), so two offline users creating records can't collide. Title and type are fixed at creation.
- **Owner's leaning for M1: one vector clock per node, shared by all records.** Each change names the record it belongs to, and "create record" is just another change in the same causal stream. Causal delivery then automatically guarantees that no edit to a record is applied before the record exists, and anti-entropy stays one exchange per peer however many records there are. The cost is that a change may occasionally wait for an unrelated record's change; that is acceptable. The alternative (one replica and one clock per record) must be compared in the M1 plan, and the owner decides.

## 5. What already exists (keep it)

Phases 1–2 of the v1 plan are built, tested and committed (current paths, before the M0 rename):
- `editor/crdt/`: ElementId, LamportClock, VectorClock, Insert/Delete/Change, RGA, diff. Unit + hypothesis tests.
- `editor/sim/`: transport (messages, envelope, `Transport` protocol), replica runtime (causal buffer, push-pull anti-entropy with `reciprocate` flag), trace events, deterministic scheduler, SimNetwork (latency, jitter, drops, reorder, partitions, offline, hold mode), Simulation facade. Unit + hypothesis end-to-end tests.
- `pyproject.toml` still names the project `collab-editor`, with no runtime dependencies or entry points yet. The README is a placeholder.

Settled decisions from that work (batch per edit; late join = bulk op-log replay; relay log in memory + optional JSON; Python 3.12 pinned via uv) stay unless the owner approves a change. Other phases of the v1 plan may also have been started; the M0 audit establishes the true state.

## 6. What must change from v1

1. **Multiple records.** The replica runtime currently holds one RGA, one clock and one history. Nodes must host many records (one RGA per record), every change must say which record it belongs to, and there must be a record-creation path. Change rules: **`rga.py`, `clocks.py`, `ids.py` and `diff.py` stay untouched.** Small additive changes to the data types in `ops.py` (for example a record field, or a create-record op) are allowed if the approved M1 design needs them. Existing tests must keep passing, adjusted only where a type gained a field.
2. **Network mode is now core**, not secondary: node process, two relays, failover, heartbeats, persistence, reconnect with backoff.
3. **Node UI** for FR1–FR5: record list with "new record", an editor pane for the open record, a status bar (relay in use, vector clock, pending count, digest), and an event log.
4. **Load generator and evaluation** for task (e).
5. **Report-ready documentation** for tasks (a) and (f).

## 7. Milestones

| ID | Milestone | Owner (proposed) | Serves | Done when |
|---|---|---|---|---|
| M0 | Setup and re-baseline | Brian | all | Audit done, rename to `fieldsync` with the new layout, plan for M1–M5 approved, repo hygiene, CI green |
| M1 | Multi-record replica runtime | Brian | b, c | Records can be created/listed/edited across simulated replicas; all old tests + new tests green; interfaces frozen |
| M2 | Network mode | Brian | b, d | Relays A/B + nodes run as separate processes; FR1–FR5 work in browser; relay A kill → failover; relay restart → recovers from JSON log |
| M3 | Simulator UI, visualisations, scenarios | Aijuka | c, d | Side-by-side replicas, network controls, message log, space-time diagram (SVG export), pending buffer view, scripted scenarios run headless in tests |
| M4 | Evaluation | Brian + Aijuka | e | Load tests P1–P4 and failure tests F1–F2 run from one command; CSV results + charts saved under `results/` |
| M5 | Documentation and demo | both | a, f | README, `docs/DESIGN.md` (report source), `docs/SCENARIOS.md`, demo script, screenshots |

M2 and M3 run **in parallel** after M1 freezes the interfaces.

### M2 detail: network mode
- `uv run relay --id A --port 8765 --peer ws://localhost:8766 --log-file relayA.json` (and the same for B).
- `uv run node --id brian --port 8081 --relays ws://localhost:8765,ws://localhost:8766`.
- Heartbeat interval and timeout are configurable (defaults about 1 s / 3 s). Reconnect uses exponential backoff with a cap.
- A relay restarted with its log file must serve late joiners correctly.
- Nodes may also persist their own replica to a local JSON file, so a node restart doesn't depend on a relay.
- Bind `0.0.0.0` so it works across a LAN, but every demo and test must also work on one laptop (localhost).

### M3 detail: simulator (priority order; cut from the bottom if late)
1. Side-by-side replica panes (text, vector clock, convergence badge) and play/pause/step controls.
2. Network controls: latency, drop, partition, hold mode with manual delivery.
3. Live space-time diagram (SVG, downloadable).
4. Message log and pending-buffer view.
5. Scripted scenarios: concurrent insert, insert + delete, out-of-order delivery, partition and heal, offline editing, late joiner, message loss.
6. CRDT internals view (element list with tombstones).

### M4 detail: evaluation
| Test | Setup | Purpose |
|---|---|---|
| P1 | 2 clients, 1 edit/s each | Baseline latency |
| P2 | 4 clients, 5 edits/s each | Moderate load |
| P3 | 8 clients, 5 edits/s each | Scaling clients |
| P4 | 16 clients, 10 edits/s each | Stress / throughput limit |
| F1 | Kill relay A during P2 | Failover recovery time |
| F2 | 30 s disconnect of half the clients during P2 | Time to converge after reconnect |

Known performance risk to check early in M4: `RGA` rebuilds its position cache after every insert, and `Replica.missing_for` scans the whole history on every sync request. Both are fine for demos but may dominate P3/P4. If profiling confirms this, fixes go through the owner, since `rga.py` is frozen.

Metrics: **sync latency** (edit made → applied on every peer; median and p95), **throughput** (changes/s through the relay), **success rate** (changes delivered, replicas converged), **recovery time** (failure → all replicas converged). Run on one laptop so all processes share a clock. Each run writes raw CSV plus a summary table, and produces charts (matplotlib) for the report.

### Stretch (only if everything above is done)
- A "lock mode" in the simulator: a pessimistic baseline where an offline replica is blocked. This shows the CRDT advantage side by side for task (c).
- Running nodes across two laptops on the same Wi-Fi for the video.

## 8. Tech stack

Python 3.12 (uv, `pyproject.toml`), ruff, pytest + hypothesis, NiceGUI, `websockets` + asyncio, matplotlib (evaluation only), Mermaid in docs.
**No library may implement the conflict-resolution logic** (no pycrdt, y-py, Automerge).

## 9. Out of scope

Authentication and access control, rich text, deleting records, editing titles, deployment, mobile layout, tombstone garbage collection (documented as a limitation).
