"""The replica runtime: CRDT + vector clock + op log + causal buffer + anti-entropy.

This code is shared by the simulator and real network mode. It is synchronous
and passive: no threads, no asyncio, no timers. The host calls
``anti_entropy()`` periodically and ``on_reconnect()`` when connectivity comes
back, and the ``Transport`` calls ``receive()`` for each incoming envelope.

Causal delivery
---------------
Every ``Change`` carries its origin's vector clock ``C``. Replica R, with
vector clock ``V``, may apply it only when

    C[origin] == V[origin] + 1          (it is the next change from origin)
    C[k] <= V[k] for every k != origin  (R already has everything origin had seen)

Otherwise the change goes into the **pending buffer** until the missing
changes arrive. This guarantees a Delete is never applied before the Insert it
targets, and an Insert never before the character it goes after. If
``C[origin] <= V[origin]``, the change is a duplicate and is ignored.

Anti-entropy (push-pull)
------------------------
``SyncRequest(V)`` means "here is my clock, send me what I'm missing". The
receiver replies with every change in its log that ``V`` does not cover. If the
receiver is itself behind, it also sends a counter-request. Because the log is
kept in *application order*, which is a causal order, the requester can apply a
response without buffering. Lost or partitioned messages are therefore
recovered as soon as any two replicas talk again.
"""

from collections.abc import Callable
from itertools import count

from editor.crdt import RGA, Change, Op, VectorClock, diff
from editor.sim.trace import EventKind, TraceEvent
from editor.sim.transport import (
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
        self.clock = VectorClock()
        self.history: list[Change] = []  # every applied change, in application (= causal) order
        self.pending: list[Change] = []  # causal buffer: received but not yet deliverable
        self.observers: list[Callable[[TraceEvent], None]] = []
        self._transport = transport
        self._now = now
        self._msg_counter = count(1)
        self._next_peer = 0  # round-robin pointer for anti-entropy
        transport.set_handler(self.receive)

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
        # The vector clock ticks once per change, not once per character.
        self.clock = self.clock.increment(self.id)
        change = Change(self.id, self.clock.get(self.id), self.clock, tuple(ops))
        self.history.append(change)
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
        # Hello is only meaningful to the relay.

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
        return [c for c in self.history if c.seq > clock.get(c.origin)]

    def waiting_on(self, change: Change) -> dict[str, int]:
        """For a buffered change: what is still missing, as origin -> highest seq needed."""
        needs: dict[str, int] = {}
        for replica, n in change.clock.items():
            required = n - 1 if replica == change.origin else n
            if required > self.clock.get(replica):
                needs[replica] = required
        return needs

    # ============================================================ internals
    def _answer_sync(self, requester: str, req: SyncRequest) -> None:
        missing = self.missing_for(req.clock)
        if missing:
            self._send(requester, SyncResponse(tuple(missing), self.clock))
        if req.reciprocate and not (req.clock <= self.clock):
            # The requester knows things we don't: pull them (counter-request ends the exchange).
            self._send(requester, SyncRequest(self.clock, reciprocate=False))

    def _deliverable(self, change: Change) -> bool:
        if change.seq != self.clock.get(change.origin) + 1:
            return False
        return all(
            n <= self.clock.get(replica)
            for replica, n in change.clock.items()
            if replica != change.origin
        )

    def _offer(self, change: Change) -> None:
        """Apply ``change`` if causally ready, else buffer it. Then drain the buffer."""
        if change.seq <= self.clock.get(change.origin) or any(
            c.dot == change.dot for c in self.pending
        ):
            self._emit("duplicate", f"{change.summary()} already seen", dot=change.dot)
            return
        if not self._deliverable(change):
            self.pending.append(change)
            needs = ", ".join(f"{r}#{n}" for r, n in self.waiting_on(change).items())
            self._emit("buffer", f"{change.summary()} waits for {needs}", dot=change.dot)
            return
        self._apply(change)
        # Applying one change may unblock buffered ones (possibly a chain of them).
        progress = True
        while progress:
            progress = False
            for buffered in list(self.pending):
                if self._deliverable(buffered):
                    self.pending.remove(buffered)
                    self._apply(buffered)
                    progress = True

    def _apply(self, change: Change) -> None:
        for op in change.ops:
            self.doc.apply(op)
        # Deliverability means this only advances clock[origin] to change.seq.
        self.clock = self.clock.merge(change.clock)
        self.history.append(change)
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
