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

### D16. Relay = CausalLog behind a WebSocket server; it answers sync itself
- **Date:** 2026-11-03 (M2)
- **Decision:** The relay reuses `CausalLog` and `answer_sync` unchanged and holds no documents. A `SyncRequest` is always answered by the relay and **never forwarded**. Relay IDs follow the replica-ID rule (`relay_a`, `relay_b`).
- **Alternatives:** Forward sync requests to all nodes; the relay runs a full `Replica`.
- **Reason:** Forwarding syncs would multiply one node's sync into a storm. Sharing the log code means relay and node can't disagree on causality, and "relays never merge" stays literally true.

### D17. Forward first-sight changes once, never duplicates
- **Date:** 2026-11-03 (M2; extended by owner decision)
- **Decision:** A relay forwards a change, as a ChangeMsg, only when `offer` returns `delivered` or `buffered`. This applies whether the change arrived as a ChangeMsg or inside a SyncResponse. It goes once to every other node and each peer relay ID, never back to its source.
- **Alternatives:** Forward ChangeMsgs only; forward everything.
- **Reason:**
  - Duplicates stopping at the first relay that has seen them makes forwarding loops impossible for any relay topology. A three-relay test proves this; with two relays, source exclusion alone would hide the bug.
  - Forwarding changes learned through sync gets rescued changes to other nodes in milliseconds after a failover.

### D18. Relay peering
- **Date:** 2026-11-03 (M2)
- **Decision:** For each `--peer` URL a relay connects as a client with `Hello("relay")`, using the same heartbeat and backoff client as nodes. It syncs with the peer on every (re)connect and every 2 s. Both relays may name each other; forwarding is de-duplicated per peer ID.
- **Alternatives:** A shared database; primary/backup replication of the log.
- **Reason:** It is the same anti-entropy protocol nodes use, with no new consistency mechanism. Either relay can serve any node.

### D19. Append-only JSON Lines persistence (relays and nodes)
- **Date:** 2026-11-03 (M2)
- **Decision:** One `Change` per line, appended on delivery or on local append, then flushed. Reload = offer the saved changes in order. A damaged last line is skipped. No `fsync`.
- **Alternatives:** Rewrite a JSON snapshot per change; SQLite; `fsync` per line.
- **Reason:**
  - Appending costs O(1) per change and the file is already in causal order.
  - Flushing survives a killed process. An OS crash may lose the last lines, but other replicas still hold those changes and anti-entropy restores them (documented limit).

### D20. Heartbeat failover, try-next-then-backoff, no flapping back
- **Date:** 2026-11-03 (M2)
- **Decision:** The node pings every 1 s. With no pong for 3 s, or a closed socket, the relay is dead and the node tries the next relay at once. After a full round of failures it backs off exponentially (0.5 s doubling, cap 10 s, ±10 % jitter). The node stays on the relay that works; after failing over to B it does not return to A when A comes back. websockets' built-in pings are off.
- **Alternatives:** Always prefer A (flap back); rely on TCP or websockets keepalive.
- **Reason:**
  - Flapping would cause extra failovers and syncs for no gain, since B is as good as A.
  - An explicit application heartbeat detects hung relays (`kill -STOP`), not just closed sockets. It also gives the RTT shown in the status bar.

### D21. Offline sends are dropped; the op log is the outbox
- **Date:** 2026-11-03 (M2)
- **Decision:** While disconnected, `WsTransport.send` drops messages. On every (re)connect the node calls `replica.on_reconnect()`, so the relay pulls whatever it lacks.
- **Alternatives:** Queue unsent messages and replay them.
- **Reason:**
  - A queue would duplicate what the op log already holds and could grow without bound.
  - The sync path is already tested and is idempotent. A test shows that removing this sync loses outage edits.

### D22. One asyncio event loop, no threads
- **Date:** 2026-11-03 (M2)
- **Decision:** In `uv run node` the `Node` runs on NiceGUI's loop (`app.on_startup`). The transport's reader, the anti-entropy timer and UI handlers all call the synchronous `Replica` from that loop.
- **Alternatives:** The network in a background thread with locks.
- **Reason:** No locks and no races on replica state. The code stays readable for the examiner.

### D23. Headless `Node` core
- **Date:** 2026-11-03 (M2)
- **Decision:** `fieldsync/net/node.py` combines replica, transport, persistence and the anti-entropy timer, with no UI import. The NiceGUI page is a thin layer on top.
- **Alternatives:** Build the node inside the UI app.
- **Reason:** Network tests and the M4 load generator run many nodes in one process without a browser.

### D24. Editor cursor anchored to CRDT element IDs
- **Date:** 2026-11-03 (M2)
- **Decision:** `RecordEditor` stores the selection as `RGA.id_before` anchors and restores it with `RGA.index_after` after a remote change. A small JS helper sets the selection.
- **Alternatives:** Keep integer cursor indices.
- **Reason:** Integer indices jump when a remote edit lands before the cursor. Element IDs never move.

### D25. Manual Offline switch on the node page
- **Date:** 2026-11-03 (M2; owner decision)
- **Decision:** `Node.set_offline(bool)` drops the connection and pauses reconnects. The status bar shows `OFFLINE (manual)`.
- **Alternatives:** Demo FR4 by stopping both relays.
- **Reason:** It shows one field officer without signal while others keep working, and it makes the offline tests simple.

### D26. Trace time is wall-clock seconds in network mode
- **Date:** 2026-11-03 (M2; owner amendment)
- **Decision:** `Node(..., now=time.time)` passes wall-clock seconds into `TraceEvent.time`. The page shows times relative to the node's start. The `TraceEvent` type is unchanged.
- **Alternatives:** Seconds since each node started.
- **Reason:** M4 measures sync latency (edit on one node → applied on every other), which needs timestamps comparable across processes. All processes run on one laptop, so they share a clock.

### D27. Node and relay IDs must be unique per device (operating rule)
- **Date:** 2026-11-03 (M2; owner amendment)
- **Decision:** Every running node and relay needs its own ID. **A node restarted without its data file must take a new ID.**
- **Alternatives:** Have the relay reject duplicate IDs.
- **Reason:**
  - Two live nodes with one ID, or a node that lost its log and restarted at seq 1, would reuse change IDs (dots). Other replicas would treat different edits as duplicates, and convergence breaks.
  - The relay can't tell a legitimate reconnect from a duplicate, so this is documented as an operating rule rather than enforced in code. The M2 smoke test hit this by accident when restarting a helper node with no data file.

### D28. Editor ↔ browser cursor sync: positions travel with their text
- **Date:** 2026-11-04 (M2 follow-up)
- **Decision:**
  - The editor sends JavaScript to its own tab (`textarea.client.run_javascript`), never through `ui.run_javascript`, because remote changes arrive on a background task with no page context.
  - Selection is reported only on user actions (keyup, click), never on `select`, which our own `setSelectionRange` fires.
  - Each report carries the browser's text. The server ignores a report whose text isn't the replica's current text, and keeps its anchors.
  - Each restore sent to the browser carries the text it was computed for. The browser applies only the newest restore, once the textarea shows that text.
  - UI observers catch their own exceptions, so a display error can't escape into the replica's causal delivery.
- **Alternatives:**
  - Integer positions with a delay (`setTimeout`): racy, and this was the failing design.
  - Sending the selection with every keystroke: more traffic, same race.
- **Reason:** A cursor index only means something in the text it was measured in. While remote edits are in flight, the browser's text lags the replica's, so unpaired indices land in the wrong place.

### D29. Replica isolates observer failures
- **Date:** 2026-11-05 (owner-approved change to frozen runtime internals; public interface unchanged)
- **Decision:** `Replica._emit` calls each observer inside its own `try/except Exception`. A failure is logged with the observer and the event, and the other observers and the causal delivery carry on. Only the observer calls are guarded: errors from the CRDT or the causal log still raise.
- **Alternatives:**
  - Rely on each observer catching its own errors (D28 does this for the UI): one forgotten `try` in new code brings the bug back.
  - Catch everything in `receive`: that would also hide real CRDT and causal-log bugs.
- **Reason:** Root cause 1 of the cursor bug (D28) was a UI observer exception escaping through `_apply` into `CausalLog.offer`, which stopped delivery of the buffered chain. Observers are outside code; the replica's state must not depend on them.
- **Also:**
  - `_emit` loops over a copy of `observers` (owner-approved). An observer that removes itself during the call (a closing UI page) used to make the next observer be skipped for that event.
  - The node's data-file writer (`Node._persist_new`) stays an observer, but its failures are loud (owner decision). An `OSError` is logged at ERROR and shown in `Node.status.persist_error` (`PersistError(message, since)`, a new optional field of `ClientStatus`). The node page shows a red "DATA FILE WRITE FAILING" label until a write succeeds. The unsaved changes are retried on every later trace event, oldest first, so the file stays complete and in causal order.
