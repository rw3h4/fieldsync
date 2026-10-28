"""Property-based evidence for Strong Eventual Consistency of the RGA.

Hypothesis generates random histories on 2-4 replicas: interleavings of local
edits (inserts and deletes at random positions) and deliveries of changes from
one replica to another, in a random but *causally valid* order. Once every
change has reached every replica, all replicas must have identical visible text
and identical internal element lists, whatever the delivery order.
"""

from dataclasses import dataclass, field

from hypothesis import given, settings
from hypothesis import strategies as st

from fieldsync.crdt import RGA, Change, VectorClock

REPLICA_IDS = ["A", "B", "C", "D"]


@dataclass
class Peer:
    rga: RGA
    clock: VectorClock = field(default_factory=VectorClock)

    def deliverable(self, change: Change) -> bool:
        """Causal delivery condition (same rule the replica runtime uses)."""
        if change.seq != self.clock.get(change.origin) + 1:
            return False
        return all(n <= self.clock.get(r) for r, n in change.clock.items() if r != change.origin)

    def deliver(self, change: Change) -> None:
        for op in change.ops:
            self.rga.apply(op)
        self.clock = self.clock.merge(change.clock)


def local_edit(peer: Peer, data: st.DataObject) -> Change | None:
    length = len(peer.rga)
    if length > 0 and data.draw(st.booleans(), label="delete?"):
        index = data.draw(st.integers(0, length - 1), label="del index")
        count = data.draw(st.integers(1, min(3, length - index)), label="del count")
        ops: list = list(peer.rga.local_delete(index, count))
    else:
        index = data.draw(st.integers(0, length), label="ins index")
        text = data.draw(st.text(alphabet="abcxyz", min_size=1, max_size=3), label="text")
        ops = list(peer.rga.local_insert(index, text))
    peer.clock = peer.clock.increment(peer.rga.replica_id)
    rid = peer.rga.replica_id
    return Change(rid, peer.clock.get(rid), peer.clock, tuple(ops), "doc")


def pending_for(peer: Peer, changes: list[Change]) -> list[Change]:
    return [c for c in changes if c.seq > peer.clock.get(c.origin) and peer.deliverable(c)]


@settings(max_examples=300, deadline=None)
@given(st.data())
def test_random_histories_converge(data: st.DataObject) -> None:
    n = data.draw(st.integers(2, 4), label="replicas")
    peers = [Peer(RGA(rid)) for rid in REPLICA_IDS[:n]]
    log: list[Change] = []

    for _ in range(data.draw(st.integers(1, 25), label="steps")):
        peer = data.draw(st.sampled_from(peers), label="peer")
        ready = pending_for(peer, log)
        if ready and data.draw(st.booleans(), label="deliver?"):
            peer.deliver(data.draw(st.sampled_from(ready), label="change"))
        else:
            change = local_edit(peer, data)
            if change is not None:
                log.append(change)

    # Quiescence: deliver everything that is still missing, in random causal order.
    for peer in peers:
        while ready := pending_for(peer, log):
            peer.deliver(data.draw(st.sampled_from(ready), label="final"))

    texts = {p.rga.text() for p in peers}
    assert len(texts) == 1, texts
    snapshots = {p.rga.snapshot() for p in peers}
    assert len(snapshots) == 1
    assert len({p.clock for p in peers}) == 1
