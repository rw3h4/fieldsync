import pytest

from fieldsync.sim.scenarios import SCENARIOS, SCENARIOS_BY_ID, get_scenario


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_every_scenario_diverges_then_converges(scenario) -> None:
    sim = scenario.setup(0)
    # In the paused setup state, replicas have not yet converged/synced.
    assert not sim.fully_synced()

    # Running recovery settles all messages and converges every replica.
    assert scenario.recover(sim)
    assert sim.converged()
    assert sim.fully_synced()

    # Every replica has the exact same record list and internal RGA snapshot per record.
    replicas = list(sim.replicas.values())
    first = replicas[0]
    for other in replicas[1:]:
        assert other.list_records() == first.list_records()
        for info in first.list_records():
            assert other.document(info.id).snapshot() == first.document(info.id).snapshot()


def test_concurrent_insert_tie_breaks_higher_id_first() -> None:
    sim = get_scenario("concurrent_insert").run(seed=0)
    # B's first element (1@B) > A's first element (1@A), so "Beta " precedes "Alpha ".
    assert set(sim.texts("A-1").values()) == {"Beta Alpha "}


def test_insert_and_delete_keeps_tombstones_and_anchor() -> None:
    sim = get_scenario("insert_and_delete").run(seed=0)
    assert set(sim.texts("A-1").values()) == {"stock: 5boxes"}
    doc = sim["A"].document("A-1")
    tombstones = [e.char for e in doc.elements() if e.deleted]
    assert tombstones == ["1", "0", " "]


def test_out_of_order_delivery_buffers_until_dependency_arrives() -> None:
    scenario = get_scenario("out_of_order_delivery")
    sim = scenario.setup(0)
    assert len(sim["B"].pending) == 1
    buffered = sim["B"].pending[0]
    assert sim["B"].waiting_on(buffered) == {"A": 2}
    assert sim["B"].text("A-1") == ""

    assert scenario.recover(sim)
    assert sim["B"].pending == []
    assert set(sim.texts("A-1").values()) == {"Day 1: Kasese"}


def test_partition_and_heal_merges_both_branches() -> None:
    scenario = get_scenario("partition_and_heal")
    sim = scenario.setup(0)
    assert sim["A"].text("A-1") == "HQ: approved"
    assert sim["B"].text("A-1") == sim["C"].text("A-1") == "HQ: reviewed "
    assert any(m.drop_reason == "partitioned" for m in sim.messages())

    assert scenario.recover(sim)
    assert set(sim.texts("A-1").values()) == {"HQ: reviewed approved"}


def test_offline_editing_propagates_offline_record_and_edits() -> None:
    scenario = get_scenario("offline_editing")
    sim = scenario.setup(0)
    assert not sim.network.is_online("B")
    assert len(sim["B"].list_records()) == 2
    assert len(sim["A"].list_records()) == 1

    assert scenario.recover(sim)
    assert len(sim["A"].list_records()) == 2
    assert set(sim.texts("B-2").values()) == {"Refer 2 patients"}
    assert set(sim.texts("A-1").values()) == {"Visits: Bwera Kasese"}


def test_late_joiner_replays_full_history() -> None:
    scenario = get_scenario("late_joiner")
    sim = scenario.setup(0)
    assert "C" in sim.replicas
    assert sim["C"].list_records() == []

    assert scenario.recover(sim)
    assert set(sim.texts("A-1").values()) == {"Clinic A: 40 doses. Clinic B: 35 doses."}


def test_message_loss_repaired_by_anti_entropy() -> None:
    scenario = get_scenario("message_loss")
    sim = scenario.setup(0)
    assert any(m.status == "dropped" and m.drop_reason == "lost" for m in sim.messages())
    assert not sim.converged()

    assert scenario.recover(sim)
    assert set(sim.texts("A-1").values()) == {"Urgent: ORS packets: 200"}


def test_scenarios_registry_covers_all_seven_brief_scenarios() -> None:
    expected = {
        "concurrent_insert",
        "insert_and_delete",
        "out_of_order_delivery",
        "partition_and_heal",
        "offline_editing",
        "late_joiner",
        "message_loss",
    }
    assert set(SCENARIOS_BY_ID) == expected
