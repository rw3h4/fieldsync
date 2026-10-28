"""The replica runtime: CRDT + vector clock + op log + causal buffer + anti-entropy.

This code is shared by the simulator and real network mode. It is synchronous
and passive: no threads, no asyncio, no timers. The host calls
``anti_entropy()`` periodically and ``on_reconnect()`` when connectivity comes
back, and the ``Transport`` calls ``receive()`` for each incoming envelope.

Causality (vector clock, op log, causal buffer, anti-entropy answers) lives in
``CausalLog`` (see ``fieldsync.runtime.log``). The replica adds the document
on top: every change the log delivers is applied to the RGA.
"""

from collections.abc import Callable
from itertools import count

from fieldsync.crdt import RGA, Change, Op, VectorClock, diff
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


class Replica:
    def __init__(
        self,
        replica_id: str,
        transport: Transport,
        now: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self.id = replica_id
        self.doc = RGA(replica_id)
        self.log = CausalLog(replica_id, on_deliver=self._apply)
        self.observers: list[Callable[[TraceEvent], None]] = []
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

    # ================================================================ local
    def edit(self, index: int, delete: int = 0, insert: str = "") -> Change | None:
        """Replace ``delete`` characters at ``index`` with ``insert`` and broadcast the change."""
        ops: list[Op] = []
        if delete:
            ops.extend(self.doc.local_delete(index, delete))
        if insert:
            ops.extend(self.doc.local_insert(index, insert))
        if not ops:
            return None
        change = self.log.append_local(tuple(ops))
        self._emit("edit", change.summary(), dot=change.dot)
        self._send(BROADCAST, ChangeMsg(change))
        return change

    def set_text(self, new_text: str) -> Change | None:
        """Editor integration: diff current text against ``new_text``, apply as one edit."""
        edit = diff(self.doc.text(), new_text)
        if edit is None:
            return None
        return self.edit(edit.index, edit.delete, edit.insert)

    def text(self) -> str:
        return self.doc.text()

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

        A late joiner has an empty clock, so the replies hold the whole op log
        (state transfer by bulk op-log replay).
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
            self._emit("duplicate", f"{change.summary()} already seen", dot=change.dot)
        elif outcome == "buffered":
            needs = ", ".join(f"{r}#{n}" for r, n in self.waiting_on(change).items())
            self._emit("buffer", f"{change.summary()} waits for {needs}", dot=change.dot)

    def _apply(self, change: Change) -> None:
        """Called by the log for each delivered remote change, in causal order."""
        for op in change.ops:
            self.doc.apply(op)
        self._emit("apply", change.summary(), dot=change.dot)

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
    ) -> None:
        event = TraceEvent(
            time=self._now(),
            replica=self.id,
            kind=kind,
            clock=self.clock,
            lamport=self.doc.clock.value,
            detail=detail,
            msg_id=msg_id,
            peer=peer,
            dot=dot,
        )
        for observer in self.observers:
            observer(event)
