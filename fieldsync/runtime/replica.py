"""The replica runtime: causal log + one RGA per record + the record registry.

This code is shared by the simulator and real network mode. It is synchronous
and passive: no threads, no asyncio, no timers. The host calls
``anti_entropy()`` periodically and ``on_reconnect()`` when connectivity comes
back, and the ``Transport`` calls ``receive()`` for each incoming envelope.

Causality (vector clock, op log, causal buffer, anti-entropy answers) lives in
``CausalLog`` (see ``fieldsync.runtime.log``). The replica adds the documents
on top: every change the log delivers is applied here.

Records
-------
The record set is a grow-only set (G-Set CRDT): records are only ever added,
and adding the same record twice is a no-op, so every replica that has seen
the same create changes has the same set. Each record has its own RGA for its
text. There is **one vector clock for all records**: creating a record is just
another change in the same causal stream, so causal delivery guarantees that
no edit is applied before the record it belongs to exists.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count

from fieldsync.crdt import (
    RGA,
    Change,
    CreateRecord,
    Op,
    RecordKind,
    VectorClock,
    check_replica_id,
    diff,
    record_id_for,
)
from fieldsync.runtime.log import CausalLog, answer_sync
from fieldsync.runtime.trace import EventKind, TraceEvent
from fieldsync.runtime.transport import (
    BROADCAST,
    ChangeMsg,
    Envelope,
    Payload,
    SyncRequest,
    SyncResponse,
    Transport,
)


@dataclass(frozen=True, slots=True)
class RecordInfo:
    """A record's fixed metadata. ``(origin, seq)`` is the dot of the change that created it."""

    id: str
    title: str
    kind: RecordKind
    origin: str
    seq: int


class UnknownRecord(KeyError):
    """The record ID is not (yet) known to this replica."""


class Replica:
    def __init__(
        self,
        replica_id: str,
        transport: Transport,
        now: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self.id = check_replica_id(replica_id)
        self.log = CausalLog(replica_id, on_deliver=self._apply)
        self.observers: list[Callable[[TraceEvent], None]] = []
        self._records: dict[str, RecordInfo] = {}  # G-Set registry: record id -> metadata
        self._docs: dict[str, RGA] = {}  # record id -> its text CRDT
        self._transport = transport
        self._now = now
        self._msg_counter = count(1)
        self._next_peer = 0  # round-robin pointer for anti-entropy
        transport.set_handler(self.receive)

    # The causal state lives in the log; these are read-only views of it.
    @property
    def clock(self) -> VectorClock:
        return self.log.clock

    @property
    def history(self) -> list[Change]:
        return self.log.history

    @property
    def pending(self) -> list[Change]:
        return self.log.pending

    # ============================================================== records
    def create_record(self, title: str, kind: RecordKind) -> str:
        """Create a new record (FR1) and broadcast it. Returns its ID."""
        # The record is named after the dot of the change that creates it, so
        # replicas can create records offline without ever colliding.
        record_id = record_id_for(self.id, self.clock.get(self.id) + 1)
        change = self.log.append_local(record_id, (CreateRecord(title, kind),))
        self._add_record(change)
        self._emit("create", change.summary(), dot=change.dot, record=record_id)
        self._send(BROADCAST, ChangeMsg(change))
        return record_id

    def list_records(self) -> list[RecordInfo]:
        """All known records (FR2), in the same order on every replica."""
        return sorted(self._records.values(), key=lambda r: (r.origin, r.seq))

    def record(self, record_id: str) -> RecordInfo:
        if record_id not in self._records:
            raise UnknownRecord(record_id)
        return self._records[record_id]

    def has_record(self, record_id: str) -> bool:
        return record_id in self._records

    # ================================================================ local
    def edit(self, record: str, index: int, delete: int = 0, insert: str = "") -> Change | None:
        """Replace ``delete`` characters at ``index`` of ``record`` with ``insert``; broadcast."""
        doc = self.document(record)
        ops: list[Op] = []
        if delete:
            ops.extend(doc.local_delete(index, delete))
        if insert:
            ops.extend(doc.local_insert(index, insert))
        if not ops:
            return None
        change = self.log.append_local(record, tuple(ops))
        self._emit("edit", change.summary(), dot=change.dot, record=record)
        self._send(BROADCAST, ChangeMsg(change))
        return change

    def set_text(self, record: str, new_text: str) -> Change | None:
        """Editor integration: diff current text against ``new_text``, apply as one edit."""
        edit = diff(self.text(record), new_text)
        if edit is None:
            return None
        return self.edit(record, edit.index, edit.delete, edit.insert)

    def text(self, record: str) -> str:
        return self.document(record).text()

    def document(self, record: str) -> RGA:
        """The record's RGA (read-only use outside this class, e.g. the internals view)."""
        if record not in self._docs:
            raise UnknownRecord(record)
        return self._docs[record]

    def digest(self, record: str) -> str:
        """Short hash of one record's text (per-record convergence badge)."""
        return self.document(record).digest()

    def state_digest(self) -> str:
        """Short hash of every record's metadata and text: equal means same visible state."""
        h = hashlib.sha1()
        for info in self.list_records():
            h.update(repr((info, self._docs[info.id].text())).encode())
        return h.hexdigest()[:8]

    # ============================================================== network
    def receive(self, env: Envelope) -> None:
        """Entry point for every incoming envelope (called by the transport)."""
        self._emit("receive", env.summary(), msg_id=env.msg_id, peer=env.src)
        p = env.payload
        if isinstance(p, ChangeMsg):
            self._offer(p.change)
        elif isinstance(p, SyncResponse):
            for change in p.changes:
                self._offer(change)
        elif isinstance(p, SyncRequest):
            self._answer_sync(env.src, p)
        # Hello and Heartbeat are handled by the network layer (relay / WsTransport).

    def anti_entropy(self) -> None:
        """One periodic anti-entropy round: ask one peer (round-robin) what we're missing."""
        peers = list(self._transport.peers())
        if peers:
            dst = peers[self._next_peer % len(peers)]
            self._next_peer += 1
        else:
            dst = BROADCAST  # peers unknown (e.g. behind a relay): ask everyone
        self._send(dst, SyncRequest(self.clock))

    def on_reconnect(self) -> None:
        """After a partition heals, coming back online or joining late: sync with everyone.

        A late joiner has an empty clock, so the replies hold the whole op log,
        record creations included (state transfer by bulk op-log replay).
        """
        self._send(BROADCAST, SyncRequest(self.clock))

    def missing_for(self, clock: VectorClock) -> list[Change]:
        """Changes in our log that a replica with ``clock`` has not seen, in causal order."""
        return self.log.missing_for(clock)

    def waiting_on(self, change: Change) -> dict[str, int]:
        """For a buffered change: what is still missing, as origin -> highest seq needed."""
        return self.log.waiting_on(change)

    # ============================================================ internals
    def _answer_sync(self, requester: str, req: SyncRequest) -> None:
        for reply in answer_sync(self.log, req):
            self._send(requester, reply)

    def _offer(self, change: Change) -> None:
        """Hand ``change`` to the causal log; it calls ``_apply`` for whatever it delivers."""
        outcome = self.log.offer(change)
        if outcome == "duplicate":
            self._emit(
                "duplicate",
                f"{change.summary()} already seen",
                dot=change.dot,
                record=change.record,
            )
        elif outcome == "buffered":
            needs = ", ".join(f"{r}#{n}" for r, n in self.waiting_on(change).items())
            self._emit(
                "buffer",
                f"{change.summary()} waits for {needs}",
                dot=change.dot,
                record=change.record,
            )

    def _apply(self, change: Change) -> None:
        """Called by the log for each delivered remote change, in causal order."""
        if change.creates_record is not None:
            self._add_record(change)
        else:
            # Causal delivery makes this lookup safe: the edit's clock covers the
            # create change, so the record was added before. UnknownRecord here
            # would mean the causal guarantee was broken.
            doc = self.document(change.record)
            for op in change.ops:
                doc.apply(op)
        self._emit("apply", change.summary(), dot=change.dot, record=change.record)

    def _add_record(self, change: Change) -> None:
        create = change.creates_record
        assert create is not None
        if change.record in self._records:
            return  # G-Set: adding twice is a no-op
        self._records[change.record] = RecordInfo(
            change.record, create.title, create.kind, change.origin, change.seq
        )
        self._docs[change.record] = RGA(self.id)

    def _send(self, dst: str, payload: Payload) -> None:
        env = Envelope(f"{self.id}:{next(self._msg_counter)}", self.id, dst, payload)
        self._emit("send", env.summary(), msg_id=env.msg_id, peer=dst)
        self._transport.send(env)

    def _emit(
        self,
        kind: EventKind,
        detail: str,
        *,
        msg_id: str | None = None,
        peer: str | None = None,
        dot: tuple[str, int] | None = None,
        record: str | None = None,
    ) -> None:
        doc = self._docs.get(record) if record is not None else None
        event = TraceEvent(
            time=self._now(),
            replica=self.id,
            kind=kind,
            clock=self.clock,
            lamport=doc.clock.value if doc is not None else 0,
            detail=detail,
            msg_id=msg_id,
            peer=peer,
            dot=dot,
            record=record,
        )
        for observer in self.observers:
            observer(event)
