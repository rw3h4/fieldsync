from fieldsync.sim.scenarios import get_scenario
from fieldsync.ui.sim.app import parse_args
from fieldsync.ui.sim.page import (
    all_known_records,
    convergence_status,
    format_groups,
    format_inflight,
    format_pending,
    format_trace_event,
    render_elements_html,
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
