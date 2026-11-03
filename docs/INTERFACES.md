# Frozen interfaces (end of M1)

Everything on this page was frozen on 2026-10-28, at the end of M1. M2 (network mode) and
M3 (simulator UI) are built against it in parallel. **Changing anything here needs owner
approval.** Adding *new* things (a new class in `net/`, a new attribute on `WsTransport`,
a new Simulation helper) is fine, as long as nothing listed here changes.

`tests/runtime/test_transport.py::test_golden_wire_format` pins the wire format byte for
byte. If it fails, a frozen interface changed: ask the owner, don't edit the expected string.

---

## 1. Wire format (`fieldsync/runtime/transport.py`)

Each message is one JSON object (one WebSocket text frame in network mode):

```json
{"type": "<kind>", "msg_id": "<sender>:<n>", "src": "<sender id>", "dst": "<id> or *", "body": {...}}
```

| `type` | Payload class | `body` |
|---|---|---|
| `change` | `ChangeMsg(change)` | a Change (below) |
| `sync_req` | `SyncRequest(clock, reciprocate=True)` | `{"clock": {"A": 2}, "reciprocate": true}` |
| `sync_resp` | `SyncResponse(changes, clock)` | `{"changes": [<Change>, ...], "clock": {...}}` (changes in causal order) |
| `hello` | `Hello(role)` | `{"role": "node"}` or `{"role": "relay"}` |
| `heartbeat` | `Heartbeat(sent_at, pong=False)` | `{"sent_at": 12.5, "pong": false}` (a pong echoes the ping's `sent_at`) |

Change and ops:

```json
{"origin": "brian", "seq": 1, "clock": {"brian": 1},
 "ops": [{"t": "new", "title": "Kasese visit", "kind": "visit_report"}], "record": "brian-1"}

{"origin": "aijuka", "seq": 3, "clock": {"aijuka": 3, "brian": 1},
 "ops": [{"t": "ins", "id": [4, "aijuka"], "after": [2, "brian"], "ch": "x"},
         {"t": "del", "target": [2, "brian"]}], "record": "brian-1"}
```

Python API: `Envelope(msg_id, src, dst, payload)`, `Envelope.kind`, `Envelope.summary()`,
`BROADCAST = "*"`, `Role = Literal["node", "relay"]`, `Payload`, `encode(env) -> str`,
`decode(text) -> Envelope`, `envelope_to_json`, `envelope_from_json`.

## 2. CRDT data types on the wire (`fieldsync/crdt/ops.py`)

- `Change(origin, seq, clock, ops, record)`: frozen. Its `.dot == (origin, seq)`, and `clock[origin] == seq`.
  It also has `.creates_record`, `.summary()`, `.to_json()` and `Change.from_json()`.
- `Insert(id, after, char)`, `Delete(target)`, `CreateRecord(title, kind)`.
  `Op = Insert | Delete | CreateRecord`.
- `RecordKind = Literal["visit_report", "case_note"]`, `RECORD_KINDS`.
- `record_id_for(origin, seq) -> "<origin>-<seq>"`. **Record IDs are opaque: never parse them.**
  Use `RecordInfo.origin` / `.seq` instead.
- `check_replica_id(rid)`: replica IDs are `[A-Za-z0-9_]+` (ASCII). Relay IDs follow the same rule.
- Rules enforced by `Change`:
  - every change names a non-empty `record`;
  - a create change holds exactly one `CreateRecord` op;
  - a create change's `record == record_id_for(origin, seq)`.
- Unchanged and frozen since v1: `ElementId(lamport, replica)`, `HEAD`, `VectorClock`, `LamportClock`, `RGA`, `diff`.

## 3. `Transport` protocol (`fieldsync/runtime/transport.py`)

```python
class Transport(Protocol):
    def send(self, envelope: Envelope) -> None: ...  # fire-and-forget; dst may be BROADCAST
    def peers(self) -> Sequence[str]: ...  # may be empty (behind a relay)
    def set_handler(self, handler: Callable[[Envelope], None]) -> None: ...
```

Delivery may be delayed, reordered, duplicated or lost; the runtime copes with all of
these. `WsTransport` (M2) may add its own attributes, e.g. connection status for FR5,
without changing the protocol. It handles `Hello` and `Heartbeat` itself and may also
pass them to the handler; `Replica` ignores both.

## 4. `CausalLog` and `answer_sync` (`fieldsync/runtime/log.py`)

```python
Offer = Literal["delivered", "buffered", "duplicate"]

class CausalLog:
    def __init__(self, owner: str, on_deliver: Callable[[Change], None] | None = None)
    owner: str
    clock: VectorClock                 # read-only
    history: list[Change]              # read-only; delivered changes in causal order
    pending: list[Change]              # read-only; the causal buffer
    def append_local(self, record: str, ops: tuple[Op, ...]) -> Change
    def offer(self, change: Change) -> Offer
    def deliverable(self, change: Change) -> bool
    def missing_for(self, clock: VectorClock) -> list[Change]
    def waiting_on(self, change: Change) -> dict[str, int]

def answer_sync(log: CausalLog, req: SyncRequest) -> list[Payload]
```

- `on_deliver(change)` is called once per delivered *remote* change, in causal order,
  after `clock` and `history` are updated. It is not called for `append_local`.
- To reload a saved log (relay or node), `offer()` the saved changes in their saved order.

## 5. `Replica` (`fieldsync/runtime/replica.py`)

```python
@dataclass(frozen=True, slots=True)
class RecordInfo: id: str; title: str; kind: RecordKind; origin: str; seq: int

class UnknownRecord(KeyError): ...

class Replica:
    def __init__(self, replica_id: str, transport: Transport, now: Callable[[], float] = lambda: 0.0)
    id: str
    log: CausalLog
    observers: list[Callable[[TraceEvent], None]]
    clock: VectorClock; history: list[Change]; pending: list[Change]   # read-only properties
    # records
    def create_record(self, title: str, kind: RecordKind) -> str
    def list_records(self) -> list[RecordInfo]        # sorted by (origin, seq): same order everywhere
    def record(self, record_id: str) -> RecordInfo    # UnknownRecord if absent
    def has_record(self, record_id: str) -> bool
    # text per record
    def edit(self, record: str, index: int, delete: int = 0, insert: str = "") -> Change | None
    def set_text(self, record: str, new_text: str) -> Change | None
    def text(self, record: str) -> str
    def document(self, record: str) -> RGA           # read-only use (internals view, cursor anchoring)
    def digest(self, record: str) -> str
    def state_digest(self) -> str                    # all records' metadata + texts
    # network
    def receive(self, env: Envelope) -> None
    def anti_entropy(self) -> None                   # one round-robin SyncRequest (BROADCAST if no peers)
    def on_reconnect(self) -> None                   # SyncRequest to BROADCAST
    def missing_for(self, clock: VectorClock) -> list[Change]
    def waiting_on(self, change: Change) -> dict[str, int]
```

## 6. Trace events (`fieldsync/runtime/trace.py`)

```python
EventKind = Literal["create", "edit", "send", "receive", "buffer", "apply", "duplicate"]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    time: float  # virtual time (sim) or seconds since start (network)
    replica: str
    kind: EventKind
    clock: VectorClock  # replica's vector clock after the event
    lamport: int  # Lamport clock of the record involved (0 if none)
    detail: str  # human-readable summary
    msg_id: str | None = None  # send/receive
    peer: str | None = None  # dst for send, src for receive
    dot: tuple[str, int] | None = None
    record: str | None = None
```

## 7. Simulation API (`fieldsync/sim/`)

```python
class Simulation:
    def __init__(self, replica_ids=("A", "B", "C"), *, seed=0, latency=0.5, jitter=0.2,
                 anti_entropy_interval: float | None = 5.0)
    seed; scheduler: Scheduler; network: SimNetwork; replicas: dict[str, Replica]
    trace: list[TraceEvent]; anti_entropy_interval: float | None
    def add_replica(self, replica_id: str, *, join: bool = True) -> Replica
    def __getitem__(self, replica_id: str) -> Replica
    # records and edits
    def create_record(self, replica_id: str, title: str, kind: RecordKind = "visit_report") -> str
    def edit(self, replica_id: str, record: str, index: int, delete: int = 0, insert: str = "") -> None
    def set_text(self, replica_id: str, record: str, text: str) -> None
    # faults
    def partition(self, groups: Sequence[Iterable[str]]) -> None
    def heal(self) -> None
    def set_online(self, replica_id: str, online: bool) -> None
    def set_hold(self, on: bool) -> None
    def deliver(self, msg_id: int) -> None
    def drop(self, msg_id: int) -> None
    def set_drop_rate(self, rate: float, links: Iterable[tuple[str, str]] | None = None) -> None
    def set_reorder(self, on: bool, links: Iterable[tuple[str, str]] | None = None) -> None
    # time
    now: float                                        # property
    def advance(self, dt: float) -> None
    def step(self) -> bool
    def settle(self, max_time: float = 120.0) -> bool
    # queries
    def texts(self, record: str) -> dict[str, str]    # "" where the record hasn't arrived
    def record_ids(self) -> dict[str, list[str]]
    def converged(self) -> bool                       # same state_digest everywhere
    def fully_synced(self) -> bool                    # same clock everywhere, nothing buffered
    def messages(self) -> list[InFlight]
```

Also frozen, because the M3 UI reads them:
- `InFlight(id, envelope, src, dst, sent_at, status, deliver_at, ended_at, drop_reason)`, where
  `status` is one of `in_flight`, `held`, `delivered` or `dropped`.
- `LinkConfig(drop_rate, reorder)`.
- `SimNetwork` read API: `latency`, `jitter`, `hold`, `messages`, `observers`, `replica_ids`,
  `peers_of`, `link`, `groups()`, `is_online`, `connected`, `held()`, `in_flight()`.
- `Scheduler`: `now`, `rng`, `step()`, `run_until()`, `next_time`, `has_foreground`.

---

## Shared but not frozen (M2): documented for M3 and M4

These may still evolve. Changes are coordinated between Brian and Aijuka rather than needing owner approval.

- `fieldsync.ui.components.editor.RecordEditor(replica, record_id=None, *, label="")`: a NiceGUI
  textarea bound to one record of any `Replica`. `.record`, `.set_record(id | None)`,
  `.refresh()`. It refreshes itself on `apply` trace events for its record and keeps the cursor
  via `fieldsync.ui.components.cursor.anchors` / `restore`.
- `fieldsync.net.node.Node(node_id, relay_urls, *, data_file=None, anti_entropy_interval=2.0,
  heartbeat_interval=1.0, heartbeat_timeout=3.0, backoff=None, now=time.time)`: a headless node.
  `.replica`, `.transport`, `.status` (`ClientStatus`), `.started_at`, `await .start()`,
  `await .stop()`, `.set_offline(bool)`.
- `fieldsync.net.relay.Relay(relay_id, *, log_file=None, peer_urls=(), anti_entropy_interval=2.0,
  heartbeat_interval=1.0, heartbeat_timeout=3.0)`: `await .start(host, port) -> port`,
  `await .stop()`, `.connect_peer(url)`, `.pause()` / `.resume()`, `.log`, `.stats`, `.node_ids`.
- `fieldsync.net.client.ClientStatus(state, url, relay_id, rtt_ms, failovers, since)`.
