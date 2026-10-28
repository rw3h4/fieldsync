"""CausalLog on its own: no RGA, no documents (this is all the M2 relay will use)."""

from fieldsync.crdt import Change, VectorClock
from fieldsync.runtime.log import CausalLog, answer_sync
from fieldsync.runtime.transport import SyncRequest, SyncResponse


def change(origin: str, seq: int, **deps: int) -> Change:
    return Change(origin, seq, VectorClock({**deps, origin: seq}), ())


def test_in_order_changes_are_delivered() -> None:
    delivered: list[tuple[str, int]] = []
    log = CausalLog("R", on_deliver=lambda c: delivered.append(c.dot))
    assert log.offer(change("A", 1)) == "delivered"
    assert log.offer(change("A", 2)) == "delivered"
    assert delivered == [("A", 1), ("A", 2)]
    assert log.clock == VectorClock({"A": 2})


def test_buffered_chain_is_released_in_causal_order() -> None:
    delivered: list[tuple[str, int]] = []
    log = CausalLog("R", on_deliver=lambda c: delivered.append(c.dot))
    b1 = change("B", 1, A=1)  # depends on A#1
    a2 = change("A", 2)  # depends on A#1 (same origin)
    assert log.offer(b1) == "buffered"
    assert log.offer(a2) == "buffered"
    assert log.waiting_on(b1) == {"A": 1}
    assert log.waiting_on(a2) == {"A": 1}
    assert log.offer(change("A", 1)) == "delivered"
    assert delivered[0] == ("A", 1)
    assert sorted(delivered[1:]) == [("A", 2), ("B", 1)]
    assert log.pending == []
    assert log.clock == VectorClock({"A": 2, "B": 1})


def test_duplicates_delivered_or_pending_are_ignored() -> None:
    log = CausalLog("R")
    a1 = change("A", 1)
    assert log.offer(a1) == "delivered"
    assert log.offer(a1) == "duplicate"
    a3 = change("A", 3)
    assert log.offer(a3) == "buffered"
    assert log.offer(a3) == "duplicate"
    assert len(log.pending) == 1 and len(log.history) == 1


def test_append_local_ticks_own_entry() -> None:
    log = CausalLog("R")
    log.offer(change("A", 1))
    mine = log.append_local(())
    assert mine.dot == ("R", 1)
    assert mine.clock == VectorClock({"A": 1, "R": 1})  # depends on everything seen
    assert log.history[-1] is mine


def test_missing_for_returns_unseen_changes_in_causal_order() -> None:
    log = CausalLog("R")
    for c in [change("A", 1), change("B", 1, A=1), change("A", 2, B=1)]:
        log.offer(c)
    assert [c.dot for c in log.missing_for(VectorClock({"A": 1}))] == [("B", 1), ("A", 2)]
    assert log.missing_for(log.clock) == []


def test_answer_sync_push_pull_and_termination() -> None:
    ahead = CausalLog("X")
    ahead.offer(change("A", 1))
    other = CausalLog("Y")
    other.offer(change("B", 1))

    # Y asks X: X has A#1 for Y, and Y has B#1 that X lacks -> response + counter-request.
    replies = answer_sync(ahead, SyncRequest(other.clock))
    assert [type(r).__name__ for r in replies] == ["SyncResponse", "SyncRequest"]
    resp, counter = replies
    assert isinstance(resp, SyncResponse) and [c.dot for c in resp.changes] == [("A", 1)]
    assert isinstance(counter, SyncRequest) and counter.reciprocate is False

    # The counter-request is answered with data only, never another request.
    assert [type(r).__name__ for r in answer_sync(other, counter)] == ["SyncResponse"]
    # Nothing to say when the requester is up to date.
    assert answer_sync(ahead, SyncRequest(ahead.clock)) == []
