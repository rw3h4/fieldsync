from fieldsync.sim import Simulation

from .conftest import shared_record


def scripted(seed: int) -> Simulation:
    s = Simulation(("A", "B", "C"), seed=seed, latency=0.5, jitter=0.4)
    rec = shared_record(s)
    s.set_drop_rate(0.3)
    s.set_reorder(True)
    s.edit("A", rec, 0, insert="hello")
    s.advance(0.2)
    s.edit("B", rec, 0, insert="world")
    s.advance(1.0)
    s.edit("C", rec, 0, insert="!")
    s.settle()
    return s


def test_same_seed_same_trace() -> None:
    a, b = scripted(42), scripted(42)
    assert a.trace == b.trace
    assert [(m.src, m.dst, m.status, m.ended_at) for m in a.messages()] == [
        (m.src, m.dst, m.status, m.ended_at) for m in b.messages()
    ]


def test_different_seed_different_trace() -> None:
    assert scripted(1).trace != scripted(2).trace


def test_concurrent_edits_converge() -> None:
    s = Simulation(("A", "B", "C"))
    rec = shared_record(s)
    s.edit("A", rec, 0, insert="aaa")
    s.edit("B", rec, 0, insert="bbb")
    s.edit("C", rec, 0, insert="ccc")
    assert s.settle()
    assert s.converged()
    assert set(s.texts(rec).values()) == {"cccbbbaaa"}


def test_lost_messages_recovered_by_anti_entropy() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=2.0)
    rec = shared_record(s)
    s.set_drop_rate(1.0)
    s.edit("A", rec, 0, insert="lost")
    s.advance(1.0)
    assert s["B"].text(rec) == ""
    s.set_drop_rate(0.0)
    assert s.settle()
    assert s["B"].text(rec) == "lost"


def test_partition_then_heal_converges() -> None:
    s = Simulation(("A", "B", "C", "D"), anti_entropy_interval=None)
    rec = shared_record(s)
    s.edit("A", rec, 0, insert="base")
    s.settle()
    s.partition([["A", "B"], ["C", "D"]])
    s.edit("A", rec, 4, insert=" left")
    s.edit("C", rec, 0, insert="right ")
    s.advance(5)
    texts = s.texts(rec)
    assert texts["A"] == texts["B"] == "base left"
    assert texts["C"] == texts["D"] == "right base"
    s.heal()  # triggers immediate anti-entropy (no periodic timer in this test)
    assert s.settle()
    assert set(s.texts(rec).values()) == {"right base left"}


def test_offline_editing_then_reconnect() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=None)
    rec = shared_record(s)
    s.edit("A", rec, 0, insert="shared")
    s.settle()
    s.set_online("B", False)
    for word in [" one", " two", " three"]:
        s.edit("B", rec, len(s["B"].text(rec)), insert=word)
    s.edit("A", rec, 0, insert=">")
    s.advance(5)
    assert not s.converged()
    s.set_online("B", True)
    assert s.settle()
    assert set(s.texts(rec).values()) == {">shared one two three"}


def test_late_joiner_catches_up() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=None)
    rec = shared_record(s)
    s.edit("A", rec, 0, insert="early ")
    s.edit("B", rec, 0, insert="bird ")
    s.settle()
    s.add_replica("C")
    assert s.settle()
    assert s["C"].text(rec) == s["A"].text(rec)


def test_trace_records_buffering() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=None)
    rec = shared_record(s)
    start = len(s.trace)  # ignore the events of the record's creation
    s.set_hold(True)
    s.edit("A", rec, 0, insert="x")
    s.edit("A", rec, 0, delete=1)
    first, second = s.network.held()
    s.deliver(second.id)
    kinds = [e.kind for e in s.trace[start:] if e.replica == "B"]
    assert kinds == ["receive", "buffer"]
    s.deliver(first.id)
    kinds = [e.kind for e in s.trace[start:] if e.replica == "B"]
    assert kinds == ["receive", "buffer", "receive", "apply", "apply"]
