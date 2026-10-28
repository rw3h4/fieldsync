# Design decisions

One entry per decision: date, decision, alternatives considered, reason.
Entries dated 2026-10-17 were made in v1 Phases 1–2 and backfilled during M0.

---

### D1. Sequence CRDT (RGA) instead of Operational Transformation
- **Date:** 2026-10-17
- **Decision:** Each record's text is an RGA sequence CRDT. Every character has a unique `(lamport, replica)` ID; deletes leave tombstones.
- **Alternatives:** OT with a central sequencer; last-writer-wins on the whole text.
- **Reason:** OT needs a central server to order operations, which is a single point of failure and blocks offline editing. LWW loses concurrent edits. RGA converges from any delivery order, so relays only forward.

### D2. Concurrent inserts at the same spot: higher ID first
- **Date:** 2026-10-17
- **Decision:** Among concurrent inserts after the same element, the one with the higher `(lamport, replica)` is placed first (closer to `after`). Integration skips every element with a greater ID.
- **Alternatives:** Lower ID first; timestamps from wall clocks.
- **Reason:** A deterministic total order that every replica computes the same way. Descendants of a sibling always have larger Lamport values, so the "skip greater IDs" rule moves past whole subtrees. Wall clocks aren't synchronised.

### D3. Batch per edit: one `Change` = one user edit
- **Date:** 2026-10-17
- **Decision:** A local edit becomes one `Change{origin, seq, clock, ops}`. The vector clock ticks once per change; causal delivery and anti-entropy work on whole changes.
- **Alternatives:** One message and one vector clock per character op.
- **Reason:** Pasting "hello" would otherwise be five messages per peer. Batching keeps logs and space-time diagrams readable and cuts metadata.

### D4. Lock-free optimistic replication with causal delivery (task c)
- **Date:** 2026-10-17 (restated in brief v2 §4)
- **Decision:** No locks. Every replica edits locally and immediately; changes carry vector clocks and are applied only when causally ready (a pending buffer holds early ones). Guarantee: strong eventual consistency.
- **Alternatives:** Pessimistic locking per record; a primary that orders all writes.
- **Reason:** Locks block offline field officers (FR4) and make the lock holder a single point of failure.

### D5. Push-pull anti-entropy with a `reciprocate` flag
- **Date:** 2026-10-17
- **Decision:** `SyncRequest(clock)` asks a peer for what we're missing. If the receiver is itself behind, it sends one counter-request with `reciprocate=False`, so the exchange always ends. Runs periodically (round-robin peer) and on reconnect.
- **Alternatives:** Push only; pull only; full-state exchange.
- **Reason:** Repairs lost or partitioned messages in both directions in one round, and can't loop.

### D6. Late joiner = bulk op-log replay
- **Date:** 2026-10-17
- **Decision:** A new replica sends an empty clock; a peer or relay answers with one `SyncResponse` holding the whole log, in causal order.
- **Alternatives:** Send a CRDT snapshot (element list + clock).
- **Reason:** Same code path as anti-entropy, so already covered by its tests. A snapshot adds a second sync path with edge cases.

### D7. Relay op log in memory, optionally persisted to JSON
- **Date:** 2026-10-17
- **Decision:** The relay keeps its op log in memory; `--log-file` persists and reloads it.
- **Alternatives:** In memory only.
- **Reason:** A restarted relay with its file can still serve late joiners (required by brief v2 M2). Without the file, nodes still recover through anti-entropy.

### D8. Deterministic simulator: virtual clock + seeded RNG; FIFO links by default
- **Date:** 2026-10-17
- **Decision:** The simulator is a discrete-event scheduler with one seeded `random.Random`. Links are FIFO unless `reorder` is turned on. Loss is decided at send time; partitions and offline are checked at delivery time.
- **Alternatives:** Real asyncio timing in the simulator; non-FIFO by default.
- **Reason:** Same seed + same inputs = identical run, so demos and tests are reproducible. FIFO by default matches TCP/WebSocket, and reordering is an explicit fault to demonstrate.

### D9. Python 3.12 pinned via uv
- **Date:** 2026-10-17 (config aligned in M0 on 2026-10-28)
- **Decision:** `.python-version` = 3.12; since M0 also `requires-python >=3.12` and ruff target `py312`.
- **Alternatives:** The system Python (3.14).
- **Reason:** NiceGUI and its dependencies may lag the newest Python. Pinning gives every developer and CI the same interpreter.

### D10. Package layout: `fieldsync/{crdt, runtime, sim}`
- **Date:** 2026-10-28 (M0)
- **Decision:** Rename `editor` to `fieldsync`. Move the replica runtime, messages/`Transport` and trace out of `sim/` into `runtime/`. Dependency direction: `sim` → `runtime` → `crdt`; `runtime` never imports `sim`.
- **Alternatives:** Keep the runtime inside `sim/` and import it from `net/`.
- **Reason:** Brief v2 §3b. The runtime is shared by the simulator and network mode, and the layout should show that the network code doesn't depend on the simulator.
