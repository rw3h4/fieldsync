"""The causal log: vector clock + op log + causal buffer + anti-entropy answers.

This is the part of a replica that knows about *causality* but not about
documents. It holds no RGA and never merges text, so the same class serves an
editor node (which applies each delivered change to its documents) and a relay
(which only stores and forwards changes).

Causal delivery
---------------
Every ``Change`` carries its origin's vector clock ``C``. A log with vector
clock ``V`` may deliver it only when

    C[origin] == V[origin] + 1          (it is the next change from origin)
    C[k] <= V[k] for every k != origin  (we already have everything origin had seen)

Otherwise the change goes into the **pending buffer** until the missing
changes arrive. This guarantees a Delete is never applied before the Insert it
targets, and an Insert never before the character it goes after. If
``C[origin] <= V[origin]``, or the change is already buffered, it is a
duplicate and is ignored.

Anti-entropy (push-pull)
------------------------
``SyncRequest(V)`` means "here is my clock, send me what I'm missing".
``answer_sync`` replies with every change in the log that ``V`` does not cover.
If the requester knows things we don't, it also sends a counter-request.
Because the log is kept in *delivery order*, which is a causal order, the
requester can deliver a response without buffering.
"""

from collections.abc import Callable
from typing import Literal

from fieldsync.crdt import Change, Op, VectorClock, check_replica_id
from fieldsync.runtime.transport import Payload, SyncRequest, SyncResponse

Offer = Literal["delivered", "buffered", "duplicate"]


class CausalLog:
    """Causal delivery and anti-entropy state for one replica or relay (no documents).

    A single vector clock covers every record: record creation and edits to
    all records form one causal stream.
    """

    def __init__(self, owner: str, on_deliver: Callable[[Change], None] | None = None) -> None:
        self.owner = check_replica_id(owner)
        self.clock = VectorClock()
        self.history: list[Change] = []  # every delivered change, in delivery (= causal) order
        self.pending: list[Change] = []  # causal buffer: received but not yet deliverable
        # Called once per delivered remote change, after clock and history are updated.
        self._on_deliver = on_deliver

    # ================================================================ local
    def append_local(self, record: str, ops: tuple[Op, ...]) -> Change:
        """Record the owner's own new change to ``record``. The clock ticks once per change."""
        self.clock = self.clock.increment(self.owner)
        change = Change(self.owner, self.clock.get(self.owner), self.clock, ops, record)
        self.history.append(change)
        return change

    # =============================================================== remote
    def offer(self, change: Change) -> Offer:
        """Deliver ``change`` if causally ready, else buffer it. Then drain the buffer."""
        if change.seq <= self.clock.get(change.origin) or any(
            c.dot == change.dot for c in self.pending
        ):
            return "duplicate"
        if not self.deliverable(change):
            self.pending.append(change)
            return "buffered"
        self._deliver(change)
        # Delivering one change may unblock buffered ones (possibly a chain of them).
        progress = True
        while progress:
            progress = False
            for buffered in list(self.pending):
                if self.deliverable(buffered):
                    self.pending.remove(buffered)
                    self._deliver(buffered)
                    progress = True
        return "delivered"

    def deliverable(self, change: Change) -> bool:
        if change.seq != self.clock.get(change.origin) + 1:
            return False
        return all(
            n <= self.clock.get(replica)
            for replica, n in change.clock.items()
            if replica != change.origin
        )

    # ============================================================== queries
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
    def _deliver(self, change: Change) -> None:
        # Deliverability means this only advances clock[origin] to change.seq.
        self.clock = self.clock.merge(change.clock)
        self.history.append(change)
        if self._on_deliver is not None:
            self._on_deliver(change)


def answer_sync(log: CausalLog, req: SyncRequest) -> list[Payload]:
    """The push-pull rule: what to send back to a ``SyncRequest``.

    - a ``SyncResponse`` with every change the requester is missing (if any);
    - a counter ``SyncRequest(reciprocate=False)`` if the requester knows
      something we don't. Counter-requests never trigger another one, so the
      exchange always ends.
    """
    replies: list[Payload] = []
    missing = log.missing_for(req.clock)
    if missing:
        replies.append(SyncResponse(tuple(missing), log.clock))
    if req.reciprocate and not (req.clock <= log.clock):
        replies.append(SyncRequest(log.clock, reciprocate=False))
    return replies
