"""Multi-record behaviour end to end through the simulator."""

from fieldsync.sim import Simulation


def test_concurrent_offline_creates_get_distinct_ids_and_spread_everywhere() -> None:
    s = Simulation(("A", "B", "C"), anti_entropy_interval=None)
    s.set_online("A", False)
    s.set_online("B", False)
    ra = s.create_record("A", "Kasese visit", "visit_report")
    rb = s.create_record("B", "Kasese visit", "visit_report")  # same title, same moment
    s.edit("A", ra, 0, insert="from A")
    s.edit("B", rb, 0, insert="from B")
    s.advance(5)
    assert ra != rb
    assert s.record_ids()["C"] == []

    s.set_online("A", True)
    s.set_online("B", True)
    assert s.settle()
    # Both records everywhere, listed in the same (origin, seq) order on every replica.
    assert s.record_ids() == {"A": [ra, rb], "B": [ra, rb], "C": [ra, rb]}
    assert s.texts(ra) == {"A": "from A", "B": "from A", "C": "from A"}
    assert s.texts(rb) == {"A": "from B", "B": "from B", "C": "from B"}
    assert s.converged()


def test_edits_to_different_records_during_partition_converge() -> None:
    s = Simulation(("A", "B", "C", "D"), anti_entropy_interval=None)
    r1 = s.create_record("A", "visit 1")
    r2 = s.create_record("C", "notes", "case_note")
    assert s.settle()
    s.partition([["A", "B"], ["C", "D"]])
    s.edit("A", r1, 0, insert="left")
    s.edit("C", r2, 0, insert="right")
    s.edit("D", r1, 0, insert="also ")
    s.advance(5)
    assert not s.converged()
    s.heal()
    assert s.settle()
    assert set(s.texts(r1).values()) == {"also left"}
    assert set(s.texts(r2).values()) == {"right"}
    assert s.converged()


def test_late_joiner_receives_all_records_and_texts() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=None)
    r1 = s.create_record("A", "one")
    r2 = s.create_record("B", "two", "case_note")
    assert s.settle()
    s.edit("A", r2, 0, insert="text two")
    s.edit("B", r1, 0, insert="text one")
    assert s.settle()
    joiner = s.add_replica("C")
    assert s.settle()
    assert [i.title for i in joiner.list_records()] == ["one", "two"]
    assert (joiner.text(r1), joiner.text(r2)) == ("text one", "text two")
    assert s.converged()


def test_not_converged_while_record_sets_differ() -> None:
    s = Simulation(("A", "B"), anti_entropy_interval=None)
    s.set_hold(True)
    rec = s.create_record("A", "pending")
    assert not s.converged()  # B has no records yet, A has one (both texts empty)
    s.set_hold(False)
    assert s.settle()
    assert s.converged() and s.texts(rec) == {"A": "", "B": ""}
