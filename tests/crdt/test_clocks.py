from editor.crdt import LamportClock, VectorClock


def test_lamport_tick_and_observe() -> None:
    clock = LamportClock()
    assert clock.tick() == 1
    clock.observe(10)
    assert clock.tick() == 11
    clock.observe(3)  # never goes backwards
    assert clock.tick() == 12


def test_vector_clock_zero_entries_are_canonical() -> None:
    assert VectorClock({"A": 0}) == VectorClock()
    assert hash(VectorClock({"A": 1, "B": 0})) == hash(VectorClock({"A": 1}))


def test_vector_clock_increment_merge() -> None:
    a = VectorClock().increment("A").increment("A")
    b = VectorClock().increment("B")
    assert a.get("A") == 2 and a.get("B") == 0
    assert a.merge(b) == VectorClock({"A": 2, "B": 1})


def test_vector_clock_partial_order() -> None:
    base = VectorClock({"A": 1})
    later = VectorClock({"A": 2, "B": 1})
    other = VectorClock({"B": 2})
    assert base <= later and base.happened_before(later)
    assert not later.happened_before(base)
    assert later.concurrent_with(other)
    assert not base.happened_before(base)
    assert base <= base


def test_vector_clock_json_round_trip() -> None:
    vc = VectorClock({"B": 3, "A": 1})
    assert VectorClock.from_dict(vc.to_dict()) == vc
    assert str(vc) == "[A:1 B:3]"
