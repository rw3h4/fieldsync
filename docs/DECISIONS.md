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

### D11. One vector clock per node, shared by all records
- **Date:** 2026-10-28 (M1)
- **Decision:** A node has one `CausalLog` (one vector clock, one op log) for all its records. Every `Change` names its record. Creating a record is just another change in the same causal stream.
- **Alternatives:** One replica and one vector clock per record.
- **Reason:**
  - Causal delivery then guarantees for free that no edit is applied before its record exists.
  - Anti-entropy stays one `SyncRequest` per peer, however many records there are, and the relay stores one log.
  - Per-record clocks would need a separate registry protocol, buffering of edits to unknown records, and O(records × nodes) sync metadata.
  - Cost: an occasional false dependency, where an edit waits for an unrelated record's change. This is bounded, because anti-entropy fills the gap.

### D12. Record ID = dot of the creating change; record IDs are opaque
- **Date:** 2026-10-28 (M1)
- **Decision:** `record_id_for(origin, seq) = "<origin>-<seq>"`, checked by `Change`. Code never parses a record ID; `RecordInfo` carries `origin` and `seq` explicitly. Title and kind are fixed at creation. The record set is a G-Set (no deletes).
- **Alternatives:** Random UUIDs; IDs assigned by the relay.
- **Reason:**
  - The dot is already globally unique, so two offline field officers can't collide, with no coordination.
  - Relay-assigned IDs would break offline creation (FR4) and give the relay an ordering role.

### D13. Replica IDs: ASCII letters, digits and underscore only
- **Date:** 2026-10-28 (M1)
- **Decision:** `check_replica_id` enforces `[A-Za-z0-9_]+` in `Change`, `CausalLog` and `Replica`.
- **Alternatives:** Any non-empty string.
- **Reason:** With no `-` allowed in replica IDs, `"<origin>-<seq>"` can never be ambiguous. IDs also stay safe in `msg_id`s, file names and URLs.

### D14. Causal log split out of the replica
- **Date:** 2026-10-28 (M1)
- **Decision:** `CausalLog` (clock, history, causal buffer, duplicate detection, `missing_for`, `waiting_on`) and `answer_sync` (the push-pull rule) live in `runtime/log.py` and hold no documents. `Replica` uses them through an `on_deliver` callback and keeps the RGAs and the record registry.
- **Alternatives:** The relay reimplements its own log; the relay runs a full `Replica`.
- **Reason:**
  - The relay must store, dedupe and serve changes but never merge them (brief §3). Sharing the exact causal code means relay and node can't disagree.
  - A relay running a full `Replica` would blur the "relays never merge" rule.

### D15. `Hello` carries a role; `Heartbeat` carries the send time
- **Date:** 2026-10-28 (M1)
- **Decision:** `Hello(role)` with role `node` or `relay`. `Heartbeat(sent_at, pong)`: the pong echoes `sent_at`. Both were added before the M1 freeze, so M2 doesn't need to reopen the wire format.
- **Alternatives:** Separate relay port for peers; WebSocket-level pings only.
- **Reason:**
  - A relay must tell nodes (forward to them) from peer relays (sync with them) on the same port.
  - Application heartbeats work in the simulator too, and the echoed time gives the round-trip time for M4.
