import pytest

from fieldsync.runtime.replica import RecordInfo, Replica, UnknownRecord
from fieldsync.runtime.transport import ChangeMsg, Envelope, SyncRequest, SyncResponse

from .conftest import ManualTransport


def make(rid: str, peers: list[str]) -> tuple[Replica, ManualTransport]:
    transport = ManualTransport(peers)
    return Replica(rid, transport), transport


def deliver(envs: list[Envelope], replica: Replica) -> None:
    for env in envs:
        replica.receive(env)


def shared_record(creator: Replica, transport: ManualTransport, *others: Replica) -> str:
    """Create one record on ``creator`` and hand its create change to ``others``."""
    rec = creator.create_record("notes", "case_note")
    envs = transport.take()
    for other in others:
        deliver(envs, other)
    return rec


def test_local_edit_produces_one_change_per_edit() -> None:
    a, ta = make("A", ["B"])
    rec = shared_record(a, ta)
    change = a.edit(rec, 0, insert="hello")
    assert change is not None and len(change.ops) == 5
    assert a.clock.get("A") == 2  # was 1: the create is change A#1
    (env,) = ta.take()
    assert isinstance(env.payload, ChangeMsg) and env.dst == "*"


def test_set_text_diffs_into_a_single_change() -> None:
    a, ta = make("A", [])
    rec = shared_record(a, ta)
    a.set_text(rec, "hello world")
    change = a.set_text(rec, "hello there")
    assert change is not None
    assert a.text(rec) == "hello there"
    assert a.clock.get("A") == 3  # was 2: the create is change A#1
    assert a.set_text(rec, "hello there") is None


def test_out_of_order_change_is_buffered_until_dependency_arrives() -> None:
    a, ta = make("A", ["B"])
    b, _ = make("B", ["A"])
    events: list[str] = []
    b.observers.append(lambda e: events.append(e.kind))
    rec = shared_record(a, ta, b)

    a.edit(rec, 0, insert="abc")
    insert_env = ta.take()
    a.edit(rec, 1, delete=1)  # delete 'b' - causally depends on the insert
    delete_env = ta.take()

    deliver(delete_env, b)  # arrives first
    assert b.text(rec) == ""
    assert len(b.pending) == 1
    assert b.waiting_on(b.pending[0]) == {"A": 2}  # was A:1: the create is change A#1
    assert "buffer" in events

    deliver(insert_env, b)
    assert b.pending == []
    assert b.text(rec) == "ac"
    assert b.clock == a.clock


def test_transitive_dependency_on_third_replica() -> None:
    a, ta = make("A", [])
    b, tb = make("B", [])
    c, _ = make("C", [])
    rec = shared_record(a, ta, b, c)
    a.edit(rec, 0, insert="x")
    from_a = ta.take()
    deliver(from_a, b)
    b.edit(rec, 1, insert="y")  # depends on A#2
    from_b = tb.take()
    deliver(from_b, c)  # C has not seen A#2 yet
    assert c.pending and c.text(rec) == ""
    deliver(from_a, c)
    assert c.text(rec) == "xy" and not c.pending


def test_duplicates_are_ignored() -> None:
    a, ta = make("A", [])
    b, _ = make("B", [])
    rec = shared_record(a, ta, b)
    a.edit(rec, 0, insert="hi")
    envs = ta.take()
    deliver(envs, b)
    deliver(envs, b)
    assert b.text(rec) == "hi"
    assert len(b.history) == 2  # was 1: the history also holds the create


def test_sync_request_returns_missing_changes_in_causal_order() -> None:
    a, ta = make("A", ["B"])
    b, tb = make("B", ["A"])
    rec = shared_record(a, ta, b)
    a.edit(rec, 0, insert="one ")
    a.edit(rec, 4, insert="two")
    a.edit(rec, 0, delete=4)
    ta.take()  # all lost

    b.anti_entropy()
    (req,) = tb.take()
    assert isinstance(req.payload, SyncRequest) and req.dst == "A"
    a.receive(req)
    (resp,) = ta.take()  # B has nothing A lacks, so no counter-request
    assert isinstance(resp.payload, SyncResponse) and len(resp.payload.changes) == 3
    b.receive(resp)
    assert b.text(rec) == "two" and not b.pending  # causal order: nothing buffered


def test_push_pull_counter_request() -> None:
    a, ta = make("A", ["B"])
    b, tb = make("B", ["A"])
    rec = shared_record(a, ta, b)
    a.edit(rec, 0, insert="a")
    b.edit(rec, 0, insert="b")
    ta.take()
    tb.take()

    a.anti_entropy()
    deliver(ta.take(), b)
    replies = tb.take()  # B sends A's missing changes AND a counter-request
    kinds = sorted(type(e.payload).__name__ for e in replies)
    assert kinds == ["SyncRequest", "SyncResponse"]
    deliver(replies, a)
    assert a.text(rec) == "ba"
    counter_replies = ta.take()
    assert [type(e.payload).__name__ for e in counter_replies] == ["SyncResponse"]
    deliver(counter_replies, b)
    assert b.text(rec) == a.text(rec) and b.clock == a.clock
    assert tb.take() == []  # exchange terminated


def test_anti_entropy_round_robin() -> None:
    a, ta = make("A", ["B", "C"])
    a.anti_entropy()
    a.anti_entropy()
    a.anti_entropy()
    assert [e.dst for e in ta.take()] == ["B", "C", "B"]


def test_late_joiner_gets_full_history() -> None:
    a, ta = make("A", [])
    rec = shared_record(a, ta)
    for word in ["alpha ", "beta ", "gamma"]:
        a.edit(rec, len(a.text(rec)), insert=word)
    ta.take()
    c, tc = make("C", [])
    c.on_reconnect()
    deliver(tc.take(), a)
    deliver(ta.take(), c)
    assert c.text(rec) == "alpha beta gamma"


# ------------------------------------------------------------ multi-record
def test_create_record_broadcasts_one_change_and_registers() -> None:
    a, ta = make("A", ["B"])
    rec = a.create_record("Kasese visit", "visit_report")
    (env,) = ta.take()
    assert isinstance(env.payload, ChangeMsg) and env.dst == "*"
    assert env.payload.change.creates_record is not None
    assert env.payload.change.record == rec
    assert a.record(rec) == RecordInfo(rec, "Kasese visit", "visit_report", "A", 1)
    assert a.text(rec) == ""


def test_records_have_independent_texts_and_stable_order() -> None:
    a, ta = make("A", [])
    b, tb = make("B", [])
    r1 = a.create_record("first", "visit_report")
    r2 = b.create_record("second", "case_note")
    deliver(ta.take(), b)
    deliver(tb.take(), a)
    a.edit(r1, 0, insert="one")
    b.edit(r2, 0, insert="two")
    deliver(ta.take(), b)
    deliver(tb.take(), a)
    for r in (a, b):
        assert [i.id for i in r.list_records()] == [r1, r2]
        assert (r.text(r1), r.text(r2)) == ("one", "two")
    assert a.state_digest() == b.state_digest()


def test_unknown_record_raises() -> None:
    a, _ = make("A", [])
    with pytest.raises(UnknownRecord):
        a.edit("Z-1", 0, insert="x")
    with pytest.raises(UnknownRecord):
        a.text("Z-1")
    assert not a.has_record("Z-1")


def test_edit_before_record_exists_is_buffered() -> None:
    a, ta = make("A", [])
    b, _ = make("B", [])
    rec = a.create_record("visit", "visit_report")
    create_env = ta.take()
    a.edit(rec, 0, insert="hi")
    edit_env = ta.take()

    deliver(edit_env, b)  # the edit overtakes the create
    assert not b.has_record(rec)
    assert len(b.pending) == 1 and b.waiting_on(b.pending[0]) == {"A": 1}

    deliver(create_env, b)  # create arrives: both apply, in causal order
    assert b.pending == []
    assert b.text(rec) == "hi"
    assert [c.dot for c in b.history] == [("A", 1), ("A", 2)]


def test_invalid_replica_id_rejected() -> None:
    with pytest.raises(ValueError):
        make("field-office", [])
