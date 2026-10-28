from fieldsync.runtime.replica import Replica
from fieldsync.runtime.transport import ChangeMsg, Envelope, SyncRequest, SyncResponse

from .conftest import ManualTransport


def make(rid: str, peers: list[str]) -> tuple[Replica, ManualTransport]:
    transport = ManualTransport(peers)
    return Replica(rid, transport), transport


def deliver(envs: list[Envelope], replica: Replica) -> None:
    for env in envs:
        replica.receive(env)


def test_local_edit_produces_one_change_per_edit() -> None:
    a, ta = make("A", ["B"])
    change = a.edit(0, insert="hello")
    assert change is not None and len(change.ops) == 5
    assert a.clock.get("A") == 1
    (env,) = ta.take()
    assert isinstance(env.payload, ChangeMsg) and env.dst == "*"


def test_set_text_diffs_into_a_single_change() -> None:
    a, _ = make("A", [])
    a.set_text("hello world")
    change = a.set_text("hello there")
    assert change is not None
    assert a.text() == "hello there"
    assert a.clock.get("A") == 2
    assert a.set_text("hello there") is None


def test_out_of_order_change_is_buffered_until_dependency_arrives() -> None:
    a, ta = make("A", ["B"])
    b, _ = make("B", ["A"])
    events: list[str] = []
    b.observers.append(lambda e: events.append(e.kind))

    a.edit(0, insert="abc")
    insert_env = ta.take()
    a.edit(1, delete=1)  # delete 'b' - causally depends on the insert
    delete_env = ta.take()

    deliver(delete_env, b)  # arrives first
    assert b.text() == ""
    assert len(b.pending) == 1
    assert b.waiting_on(b.pending[0]) == {"A": 1}
    assert "buffer" in events

    deliver(insert_env, b)
    assert b.pending == []
    assert b.text() == "ac"
    assert b.clock == a.clock


def test_transitive_dependency_on_third_replica() -> None:
    a, ta = make("A", [])
    b, tb = make("B", [])
    c, _ = make("C", [])
    a.edit(0, insert="x")
    from_a = ta.take()
    deliver(from_a, b)
    b.edit(1, insert="y")  # depends on A#1
    from_b = tb.take()
    deliver(from_b, c)  # C has not seen A#1 yet
    assert c.pending and c.text() == ""
    deliver(from_a, c)
    assert c.text() == "xy" and not c.pending


def test_duplicates_are_ignored() -> None:
    a, ta = make("A", [])
    b, _ = make("B", [])
    a.edit(0, insert="hi")
    envs = ta.take()
    deliver(envs, b)
    deliver(envs, b)
    assert b.text() == "hi"
    assert len(b.history) == 1


def test_sync_request_returns_missing_changes_in_causal_order() -> None:
    a, ta = make("A", ["B"])
    b, tb = make("B", ["A"])
    a.edit(0, insert="one ")
    a.edit(4, insert="two")
    a.edit(0, delete=4)
    ta.take()  # all lost

    b.anti_entropy()
    (req,) = tb.take()
    assert isinstance(req.payload, SyncRequest) and req.dst == "A"
    a.receive(req)
    (resp,) = ta.take()  # B has nothing A lacks, so no counter-request
    assert isinstance(resp.payload, SyncResponse) and len(resp.payload.changes) == 3
    b.receive(resp)
    assert b.text() == "two" and not b.pending  # causal order: nothing buffered


def test_push_pull_counter_request() -> None:
    a, ta = make("A", ["B"])
    b, tb = make("B", ["A"])
    a.edit(0, insert="a")
    b.edit(0, insert="b")
    ta.take()
    tb.take()

    a.anti_entropy()
    deliver(ta.take(), b)
    replies = tb.take()  # B sends A's missing changes AND a counter-request
    kinds = sorted(type(e.payload).__name__ for e in replies)
    assert kinds == ["SyncRequest", "SyncResponse"]
    deliver(replies, a)
    assert a.text() == "ba"
    counter_replies = ta.take()
    assert [type(e.payload).__name__ for e in counter_replies] == ["SyncResponse"]
    deliver(counter_replies, b)
    assert b.text() == a.text() and b.clock == a.clock
    assert tb.take() == []  # exchange terminated


def test_anti_entropy_round_robin() -> None:
    a, ta = make("A", ["B", "C"])
    a.anti_entropy()
    a.anti_entropy()
    a.anti_entropy()
    assert [e.dst for e in ta.take()] == ["B", "C", "B"]


def test_late_joiner_gets_full_history() -> None:
    a, ta = make("A", [])
    for word in ["alpha ", "beta ", "gamma"]:
        a.edit(len(a.text()), insert=word)
    ta.take()
    c, tc = make("C", [])
    c.on_reconnect()
    deliver(tc.take(), a)
    deliver(ta.take(), c)
    assert c.text() == "alpha beta gamma"
