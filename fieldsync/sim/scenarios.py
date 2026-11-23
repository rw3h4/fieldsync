"""Scripted simulator scenarios for the M3 UI and headless tests.

Each ``Scenario`` has two stages:

1. ``setup(seed=0)`` builds a ``Simulation``, creates a shared record, and stops
   at the moment a fault or concurrency anomaly is visible (in-flight concurrent
   edits, a buffered out-of-order change, an active partition, an offline
   replica, a late joiner awaiting sync, or dropped messages). The M3 UI loads
   this state so the presenter can inspect it and step/play forward.
2. ``recover(sim)`` heals any active fault (partition, offline, hold mode, drop
   rate) and runs ``sim.settle()`` so the replicas converge. ``run(seed=0)``
   runs both stages end to end for headless verification.

For live narration each scenario also has two captions, one per stage: the UI
shows ``setup_caption`` after loading and ``recover_caption`` after its Next
button has run ``recover`` (D33).
"""

from collections.abc import Callable
from dataclasses import dataclass

from fieldsync.sim.simulation import Simulation


def _heal_all(sim: Simulation) -> bool:
    """Restore full connectivity and run until all replicas are synced."""
    sim.set_hold(False)
    sim.set_drop_rate(0.0)
    sim.set_reorder(False)
    for rid in sim.replicas:
        sim.set_online(rid, True)
    sim.heal()
    return sim.settle()


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named, reproducible simulation scenario."""

    id: str
    title: str
    description: str
    setup: Callable[[int], Simulation]
    recover: Callable[[Simulation], bool] = _heal_all
    setup_caption: str = ""  # what to point at in the paused setup state
    recover_caption: str = ""  # what to point at after recover() has settled

    def run(self, seed: int = 0) -> Simulation:
        """Run setup followed by recovery; return the settled Simulation."""
        sim = self.setup(seed)
        self.recover(sim)
        return sim


# ------------------------------------------------------------------ scenarios
def setup_concurrent_insert(seed: int = 0) -> Simulation:
    """Two replicas insert at the same position concurrently (RGA tie-break)."""
    sim = Simulation(("A", "B", "C"), seed=seed)
    rec = sim.create_record("A", "Kasese clinic visit", "visit_report")
    sim.settle()
    # Both A and B insert at index 0 before receiving each other's edit.
    sim.edit("A", rec, 0, insert="Alpha ")
    sim.edit("B", rec, 0, insert="Beta ")
    return sim


def setup_insert_and_delete(seed: int = 0) -> Simulation:
    """One replica inserts inside a span while another deletes the surrounding text."""
    sim = Simulation(("A", "B", "C"), seed=seed)
    rec = sim.create_record("A", "Malaria stock note", "case_note")
    sim.edit("A", rec, 0, insert="stock: 10 boxes")
    sim.settle()
    # A inserts '5' after '1' (making '150'); B concurrently deletes '10 '.
    # Because deletes leave tombstones, A's '5' still finds its '1' anchor.
    sim.edit("A", rec, 8, insert="5")
    sim.edit("B", rec, 7, delete=3)
    return sim


def setup_out_of_order_delivery(seed: int = 0) -> Simulation:
    """Hold mode delivers A's second edit before its first; B buffers the early change."""
    sim = Simulation(("A", "B"), seed=seed, anti_entropy_interval=None)
    rec = sim.create_record("A", "Outreach schedule", "visit_report")
    sim.settle()
    sim.set_hold(True)
    sim.edit("A", rec, 0, insert="Day 1")
    sim.edit("A", rec, 5, insert=": Kasese")
    first, second = sim.network.held()
    # Deliver the second change first; first stays held so B buffers second.
    sim.deliver(second.id)
    assert first.status == "held"
    return sim


def setup_partition_and_heal(seed: int = 0) -> Simulation:
    """Network splits into [A] and [B, C]; both sides edit independently."""
    sim = Simulation(("A", "B", "C"), seed=seed, anti_entropy_interval=None)
    rec = sim.create_record("A", "District summary", "visit_report")
    sim.edit("A", rec, 0, insert="HQ: ")
    sim.settle()
    sim.partition([["A"], ["B", "C"]])
    sim.edit("A", rec, 4, insert="approved")
    sim.edit("B", rec, 4, insert="reviewed ")
    sim.advance(2.0)
    return sim


def setup_offline_editing(seed: int = 0) -> Simulation:
    """Replica B goes offline, edits and creates a record while A edits online."""
    sim = Simulation(("A", "B", "C"), seed=seed)
    rec = sim.create_record("A", "Field report", "visit_report")
    sim.edit("A", rec, 0, insert="Visits:")
    sim.settle()
    sim.set_online("B", False)
    sim.edit("B", rec, len(sim["B"].text(rec)), insert=" Bwera")
    offline_rec = sim.create_record("B", "Bwera follow-up", "case_note")
    sim.edit("B", offline_rec, 0, insert="Refer 2 patients")
    sim.edit("A", rec, len(sim["A"].text(rec)), insert=" Kasese")
    sim.advance(1.5)
    return sim


def setup_late_joiner(seed: int = 0) -> Simulation:
    """Replicas A and B build history; replica C joins late and requests a full sync."""
    sim = Simulation(("A", "B"), seed=seed)
    rec = sim.create_record("A", "Immunisation log", "visit_report")
    sim.edit("A", rec, 0, insert="Clinic A: 40 doses. ")
    sim.settle()
    sim.edit("B", rec, len(sim["B"].text(rec)), insert="Clinic B: 35 doses.")
    sim.settle()
    sim.add_replica("C", join=True)
    return sim


def setup_message_loss(seed: int = 0) -> Simulation:
    """100% packet loss drops broadcast edits; periodic anti-entropy repairs them."""
    sim = Simulation(("A", "B", "C"), seed=seed, anti_entropy_interval=2.0)
    rec = sim.create_record("A", "Supply request", "case_note")
    sim.settle()
    sim.set_drop_rate(1.0)
    sim.edit("A", rec, 0, insert="ORS packets: 200")
    sim.edit("B", rec, 0, insert="Urgent: ")
    sim.advance(1.0)
    # Restore the link so the next anti-entropy round repairs the lost edits.
    sim.set_drop_rate(0.0)
    return sim


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="concurrent_insert",
        title="Concurrent insert",
        description=(
            "Replicas A and B insert at index 0 at the same time. "
            "RGA orders concurrent siblings deterministically by higher (lamport, replica) ID."
        ),
        setup=setup_concurrent_insert,
        setup_caption=(
            "Setup: A typed 'Alpha ' and B typed 'Beta ' at index 0; neither edit has arrived at "
            "the other side yet, so the texts differ. Press Next to deliver everything."
        ),
        recover_caption=(
            "Recovered: every replica shows 'Beta Alpha '. Both inserts follow HEAD, and the "
            "higher element ID (1@B > 1@A) goes first, on every replica, with no coordination."
        ),
    ),
    Scenario(
        id="insert_and_delete",
        title="Concurrent insert + delete",
        description=(
            "Replica A inserts a character inside '10 ' while Replica B deletes '10 '. "
            "Deletes leave tombstones, so A's insert still anchors cleanly."
        ),
        setup=setup_insert_and_delete,
        setup_caption=(
            "Setup: A inserted '5' after the '1' of '10 ' while B deleted '10 '. In the CRDT "
            "internals, A and B each have only their own edit so far. Press Next."
        ),
        recover_caption=(
            "Recovered: the deleted characters stay as red tombstones, and A's '5' is still "
            "anchored after its '1' tombstone, so it survives the delete on every replica."
        ),
    ),
    Scenario(
        id="out_of_order_delivery",
        title="Out-of-order delivery (causal buffer)",
        description=(
            "Hold mode delivers A#3 to B before A#2. B holds A#3 in its pending causal "
            "buffer until A#2 is delivered, then applies both in order."
        ),
        setup=setup_out_of_order_delivery,
        setup_caption=(
            "Setup: hold mode is on. A's second edit (A#3) was delivered to B before the first "
            "(A#2), so B keeps A#3 in its pending causal buffer: 'waits for A#2'. Press Next (or "
            "Deliver the held message)."
        ),
        recover_caption=(
            "Recovered: A#2 arrived, B applied A#2 and then the buffered A#3, in causal order. "
            "The buffer is empty and both texts match."
        ),
    ),
    Scenario(
        id="partition_and_heal",
        title="Network partition and heal",
        description=(
            "A is partitioned from [B, C]; both sides edit the same record. "
            "Healing triggers anti-entropy and merges both branches."
        ),
        setup=setup_partition_and_heal,
        setup_caption=(
            "Setup: A is cut off from [B, C]. Both sides edited the same record; red crosses (✕) "
            "in the diagram are messages dropped by the partition. Press Next to heal."
        ),
        recover_caption=(
            "Recovered: after healing, anti-entropy (sync_req / sync_resp) exchanged the missing "
            "changes and both branches merged."
        ),
    ),
    Scenario(
        id="offline_editing",
        title="Offline editing and reconnect",
        description=(
            "Field officer B goes offline, edits an existing record and creates a new "
            "record while A edits online. Reconnecting merges everything."
        ),
        setup=setup_offline_editing,
        setup_caption=(
            "Setup: B is offline. It edited the shared record and created a new one, while A kept"
            " editing online. Press Next to bring B back."
        ),
        recover_caption=(
            "Recovered: B reconnected and synced. Its offline edit and its new record reached A "
            "and C, and A's edit reached B."
        ),
    ),
    Scenario(
        id="late_joiner",
        title="Late joiner (bulk op-log replay)",
        description=(
            "Replicas A and B create and edit a record; Replica C joins afterwards with "
            "an empty clock and catches up via SyncResponse replay."
        ),
        setup=setup_late_joiner,
        setup_caption=(
            "Setup: A and B built up history; C has just joined with an empty clock and sent a "
            "sync_req. Press Next."
        ),
        recover_caption=(
            "Recovered: C caught up by replaying the change log it received in sync_resp "
            "messages, and now matches A and B."
        ),
    ),
    Scenario(
        id="message_loss",
        title="Message loss repaired by anti-entropy",
        description=(
            "All broadcast ChangeMsgs during an edit burst are lost on the wire. "
            "Periodic push-pull anti-entropy detects the clock gap and repairs them."
        ),
        setup=setup_message_loss,
        setup_caption=(
            "Setup: drop rate was 100% during an edit burst, so every broadcast edit was lost "
            "(red ✕ in the diagram). The link is restored, but the replicas still differ. Press "
            "Next."
        ),
        recover_caption=(
            "Recovered: periodic anti-entropy saw the clock gap and its sync_resp repaired the "
            "lost edits; all replicas converged."
        ),
    ),
)

SCENARIOS_BY_ID: dict[str, Scenario] = {s.id: s for s in SCENARIOS}


def get_scenario(scenario_id: str) -> Scenario:
    """Return a scenario by ID, or raise ``KeyError``."""
    return SCENARIOS_BY_ID[scenario_id]
