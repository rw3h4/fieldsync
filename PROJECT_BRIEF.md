# Project Brief: Distributed Collaborative Text Editor (Python)

## Context

This is a university **Distributed Systems** course project. The assignment is to **design and illustrate the operation of a distributed system**. The grade depends on how clearly the project demonstrates distributed-systems concepts (concurrency, causality, replication, consistency, fault tolerance), **not** on production polish.

So the priorities, in order:
1. A correct, well-explained conflict-resolution core (a CRDT).
2. **Observability**: the distributed behaviour must be *visible* on screen (divergence, message flow, clocks, convergence).
3. A clear design document with diagrams.
4. A real multi-process networked mode (secondary to the simulator).

Out of scope: authentication, rich text, persistence beyond simple in-memory/JSON, deployment, mobile layout.

## Tech stack

- **Language:** Python 3.11+, fully type-hinted.
- **Project tooling:** `uv` + `pyproject.toml`; `ruff` for lint/format.
- **UI:** **NiceGUI** (Python-defined UI rendered in the browser, live updates over WebSocket). A small amount of custom JavaScript is allowed **only** where NiceGUI can't do something (e.g. reading/restoring textarea cursor position via `ui.run_javascript`). All distributed-systems logic stays in Python.
- **Real networking:** `asyncio` + `websockets` (or FastAPI WebSockets) for the relay server and replica nodes.
- **Tests:** `pytest` + **`hypothesis`** (property-based testing).
- **Diagrams in docs:** Mermaid. Space-time diagrams are generated as SVG by the app.
- Commands: `uv run sim` (simulator UI), `uv run relay` + `uv run node --id A --port 8081` (network mode), `uv run pytest`.

Package layout (single package `editor/`):
- `editor/crdt/` - pure CRDT + clocks. No I/O, no asyncio, no UI imports.
- `editor/sim/` - simulated network + replica runtime (causal buffer, anti-entropy).
- `editor/ui/` - NiceGUI pages and visualisation components.
- `editor/net/` - relay server and real replica node.
- `tests/`

**Important:** Do NOT use `pycrdt`, `y-py`, Automerge bindings, or any library that implements the conflict-resolution logic. The point of the project is to show that logic. General utilities are fine.

## Core design

### Data model: sequence CRDT (RGA-style)

- Each character is an element with a globally unique ID `(lamport, replica_id)`.
- Insert ops reference the ID of the element they are inserted **after** (or a head sentinel).
- Concurrent inserts after the same element are ordered deterministically (higher `(lamport, replica_id)` first; document the rule).
- Deletes mark elements as **tombstones** (not removed), so later ops can still reference them.
- Ops (frozen dataclasses, JSON-serialisable): `Insert(id, after_id, char)`, `Delete(target_id)`. Each op also carries its origin replica and a vector clock / dependency info.
- Guarantee to aim for and document: **Strong Eventual Consistency** - any two replicas that have received the same set of ops have identical visible text, regardless of delivery order.

### Clocks and causality

- **Lamport clock** per replica, used for element IDs.
- **Vector clock** per replica, attached to each op, used for:
  - **causal delivery**: an op whose dependencies haven't arrived (e.g. a delete of a not-yet-seen char) is held in a **pending buffer** and applied once deps arrive. This must be visible in the UI.
  - **anti-entropy / sync**: on (re)connect, replicas exchange vector clocks and send each other the ops the other is missing.

### Simulated network

- Implement as a **deterministic discrete-event simulator** with a virtual clock and a seeded RNG, so every scenario is exactly reproducible (important for demos and tests). The UI advances virtual time on a timer, with play/pause/step and speed controls.
- Supports per-link latency (with jitter), drop probability, reordering, partitions, and a **"hold" mode** where messages queue as in-flight items that the presenter delivers manually in any order.
- The same replica runtime code (causal buffer, anti-entropy) must run in both the simulator and real network mode: only the transport differs. Define a small `Transport` interface for this.

### Editor integration

- One NiceGUI textarea per replica. On change, diff old vs new text (single changed range is enough) to produce delete + insert ops against the CRDT.
- After remote ops are applied, update the text and **preserve the local cursor** by anchoring it to a CRDT element ID, not an index (small JS helper to read/set `selectionStart`/`selectionEnd`).

### Architecture modes

1. **Simulator mode (main demo):** 2–4 replicas rendered side-by-side on one NiceGUI page, all living in one Python process and connected through the simulated network the presenter controls.
2. **Network mode:** each replica is a **separate OS process** (`uv run node --id A --port 8081`), each serving its own small NiceGUI editor page, connected to a relay (`uv run relay`). Nodes can run on different machines on the same LAN. The relay is deliberately **dumb**: it forwards ops and keeps an op log to help late joiners catch up, but does NOT impose an order or resolve conflicts. Convergence must come from the CRDT alone. (Document why: the design doesn't depend on a central sequencer, unlike OT.)

## Visualisations (the "illustrate" part)

In simulator mode, the UI must include:

1. **Side-by-side replica panes**, each with its replica ID, current text, vector clock, and a **convergence badge** (short hash of visible text; all badges match = converged).
2. **Network control panel:** global latency slider, per-link drop/reorder toggles, a **partition** control (split replicas into groups), and a **hold-messages mode** where in-flight messages appear as cards that can be delivered manually, in any order. Plus play/pause/step for virtual time.
3. **Message log** per replica: sent/received/buffered/applied events with op summary and vector clock.
4. **Live space-time diagram** (SVG generated in Python): one vertical line per replica, a dot per event, arrows for messages (dropped messages shown as broken arrows), with tooltips showing clocks. Must have a "download SVG" button for the report.
5. **CRDT internals view:** the element list for a chosen replica, showing each character's ID, its `after_id`, and tombstones greyed out.
6. **Pending/causal buffer view:** ops waiting on missing dependencies.

## Scripted scenarios

A dropdown that resets the sim (fixed seed) and plays a scenario step by step (with Next/Auto buttons and a one-line caption explaining what's being demonstrated):

1. **Concurrent insert at the same position** - both replicas type at the same spot; show tie-break ordering and convergence.
2. **Concurrent insert + delete** - one replica deletes a word while another inserts inside it.
3. **Out-of-order delivery** - a delete arrives before the insert it targets; show it waiting in the causal buffer, then applying.
4. **Network partition and heal** - both sides edit during partition; on heal, anti-entropy syncs and they converge.
5. **Offline editing** - one replica goes offline, makes many edits, reconnects.
6. **Late joiner** - a new replica joins mid-session and catches up via state transfer.
7. **Message loss** - dropped messages are recovered through anti-entropy.

Scenarios should be defined as data/scripts against the sim API (not hard-coded in the UI), so they can also run headless in tests.

## Testing

- Unit tests for the CRDT (insert, delete, tie-breaks, tombstones, serialisation).
- **Property-based convergence test** (`hypothesis`): generate random ops on N replicas, deliver them in random orders (respecting causal delivery via the buffer), assert all replicas end with identical text. This is the key evidence of correctness; mention it in the docs.
- Tests for causal buffering and anti-entropy.
- Every scripted scenario runs headless in a test and asserts convergence.

## Documentation deliverables

- `docs/DESIGN.md` covering: problem statement; architecture (Mermaid diagram); data model; the CRDT algorithm with a worked example of two concurrent edits; clocks and causal delivery; consistency guarantee (SEC) and what is NOT guaranteed; CAP positioning (AP, with convergence on heal); fault tolerance (offline, partition, loss, relay crash, late join); OT vs CRDT comparison and why CRDT was chosen; trade-offs (tombstone growth, metadata overhead, Python performance); limitations and future work.
- `docs/SCENARIOS.md`: for each scripted scenario, what it demonstrates, the expected outcome, and its space-time diagram (exported SVG).
- `README.md`: how to install, run, test, and present the demo (including running nodes on multiple machines).

## How to work

- **Start in plan mode.** Read this brief, then propose: the package structure, the CRDT data structures and op types, the message format, the `Transport` interface, and a phased plan. **Wait for my approval before writing code.**
- Then build in phases, committing to git at the end of each phase with a clear message, and giving me a short summary before moving on:
  1. Project scaffold + `editor/crdt` with unit and hypothesis tests passing.
  2. `editor/sim` (discrete-event network, replica runtime, causal buffer, anti-entropy) with tests.
  3. `editor/ui` basic simulator: replica panes, network controls, time controls, convergence badges.
  4. Visualisations: message log, space-time diagram, CRDT internals, pending buffer.
  5. Scripted scenarios (+ headless scenario tests).
  6. `editor/net` network mode (relay + node processes).
  7. Docs.
- Phases 1–2 must be done and tested before parallel work starts. After that, subagents may work in parallel on independent pieces (e.g. visualisations vs network mode vs docs), building against the agreed interfaces.
- Keep code readable and commented where the distributed-systems logic happens: an examiner may read it.
- If anything in this brief is ambiguous or seems wrong, ask rather than guess.
