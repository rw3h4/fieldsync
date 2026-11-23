import logging

import pytest

from fieldsync.sim.scenarios import get_scenario
from fieldsync.ui.sim.app import parse_args, sim_url
from fieldsync.ui.sim.page import (
    all_known_records,
    convergence_status,
    diagram_key,
    format_groups,
    format_inflight,
    format_pending,
    format_trace_event,
    internals_key,
    pending_key,
    queue_key,
    render_elements_html,
    run_guarded,
    speed_label,
)


def test_sim_cli_defaults_and_overrides() -> None:
    defaults = parse_args([])
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 8080
    assert defaults.seed == 0

    custom = parse_args(["--host", "0.0.0.0", "--port", "9090", "--seed", "42"])
    assert custom.host == "0.0.0.0"
    assert custom.port == 9090
    assert custom.seed == 42
    assert sim_url(defaults) == "http://127.0.0.1:8080/"
    assert sim_url(custom) == "http://0.0.0.0:9090/"


def test_convergence_badge_and_pending_formatter() -> None:
    scenario = get_scenario("out_of_order_delivery")
    sim = scenario.setup(0)

    badge_text, _ = convergence_status(sim)
    assert "DIVERGED" in badge_text

    pending = format_pending(sim["B"])
    assert len(pending) == 1
    assert "waits for A#2" in pending[0]

    scenario.recover(sim)
    badge_after, _ = convergence_status(sim)
    assert "CONVERGED & SYNCED" in badge_after
    assert format_pending(sim["B"]) == []


def test_elements_html_renders_visible_and_tombstones() -> None:
    sim = get_scenario("insert_and_delete").run(0)
    elements = sim["A"].document("A-1").elements()
    html = render_elements_html(elements)
    assert "tombstone" in html
    assert "line-through" in html
    assert "visible" in html
    assert "␣" in html


def test_topology_and_log_formatters() -> None:
    scenario = get_scenario("partition_and_heal")
    sim = scenario.setup(0)
    assert "Partitioned:" in format_groups(sim)
    assert "[A]" in format_groups(sim)
    assert "[B, C]" in format_groups(sim)

    msg_lines = [format_inflight(m) for m in sim.messages()]
    assert any("partitioned" in line for line in msg_lines)

    trace_lines = [format_trace_event(e) for e in sim.trace]
    assert any("create" in line for line in trace_lines)
    assert any("edit" in line for line in trace_lines)

    records = all_known_records(sim)
    assert [r.id for r in records] == ["A-1"]


# ----------------------------------------------------------- change keys (D32)
def all_keys(sim, include_sync=True):
    return (
        queue_key(sim),
        tuple(pending_key(r) for r in sim.replicas.values()),
        diagram_key(sim, include_sync),
        internals_key(sim, "A-1"),
    )


def test_keys_are_stable_while_nothing_happens() -> None:
    """An idle tick (paused, hold mode) must not rebuild the queue and its buttons."""
    sim = get_scenario("out_of_order_delivery").setup(0)
    assert sim.network.held(), "scenario should hold a message"
    before = all_keys(sim)
    for _ in range(5):
        assert all_keys(sim) == before


def test_manual_deliver_changes_queue_pending_diagram_and_internals_keys() -> None:
    sim = get_scenario("out_of_order_delivery").setup(0)
    (held,) = sim.network.held()
    queue, pending, diagram, internals = all_keys(sim)
    now = sim.now
    sim.deliver(held.id)
    assert sim.now == now  # a manual delivery does not move the clock ...
    assert queue_key(sim) != queue  # ... but the queue, the causal buffer,
    assert tuple(pending_key(r) for r in sim.replicas.values()) != pending
    assert diagram_key(sim, True) != diagram  # the diagram/logs (a status changed)
    assert internals_key(sim, "A-1") != internals  # and B's clock all change


def test_manual_drop_changes_queue_and_diagram_keys() -> None:
    sim = get_scenario("out_of_order_delivery").setup(0)
    (held,) = sim.network.held()
    queue, _, diagram, _ = all_keys(sim)
    sim.drop(held.id)
    assert queue_key(sim) != queue
    assert diagram_key(sim, True) != diagram


def test_advance_edit_and_switch_change_the_right_keys() -> None:
    sim = get_scenario("concurrent_insert").setup(0)
    diagram = diagram_key(sim, True)
    assert diagram_key(sim, False) != diagram  # sync-arrows switch redraws
    sim.advance(0.05)
    assert diagram_key(sim, True) != diagram  # sim.now moved
    internals = internals_key(sim, "A-1")
    sim.edit("C", "A-1", 0, insert="x")
    assert internals_key(sim, "A-1") != internals
    assert internals_key(sim, None) != internals  # selecting another record redraws


# -------------------------------------------------------------- panel guards
def test_run_guarded_logs_and_shows_the_error_in_that_panel(caplog) -> None:
    shown: list[str | None] = []

    def broken() -> None:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="fieldsync.ui.sim.page"):
        assert run_guarded("queue", broken, shown.append) is False
    assert shown == ["view error in queue: ValueError: boom"]
    assert "simulator view 'queue' failed" in caplog.text

    assert run_guarded("queue", lambda: None, shown.append) is True
    assert shown[-1] is None  # the error clears once the panel refreshes again


def test_run_guarded_does_not_swallow_keyboard_interrupt() -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_guarded("diagram", interrupted, lambda _: None)


def test_speed_label() -> None:
    assert speed_label(0.15) == "0.15 s/tick ≈ 0.75x real time"
    assert speed_label(1.0) == "1.00 s/tick ≈ 5.00x real time"
