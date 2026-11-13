import xml.etree.ElementTree as ET

from fieldsync.sim.scenarios import get_scenario
from fieldsync.sim.simulation import Simulation
from fieldsync.ui.sim.spacetime import render_spacetime_svg


def test_spacetime_svg_is_valid_xml_and_lists_replicas() -> None:
    sim = Simulation(("A", "B", "C"))
    rec = sim.create_record("A", "Visit <1> & notes", "visit_report")
    sim.edit("A", rec, 0, insert="hello")
    sim.settle()

    svg = render_spacetime_svg(sim)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    for rid in ("A", "B", "C"):
        assert f">{rid}</text>" in svg
    assert "msg-delivered" in svg
    assert "trace-create" in svg
    assert "trace-edit" in svg
    assert "trace-apply" in svg


def test_spacetime_svg_renders_dropped_held_and_buffered_states() -> None:
    loss_sim = get_scenario("message_loss").setup(0)
    loss_svg = render_spacetime_svg(loss_sim)
    ET.fromstring(loss_svg)
    assert "msg-dropped" in loss_svg
    assert "✕" in loss_svg

    ooo_sim = get_scenario("out_of_order_delivery").setup(0)
    ooo_svg = render_spacetime_svg(ooo_sim)
    ET.fromstring(ooo_svg)
    assert "msg-held" in ooo_svg
    assert "trace-buffer" in ooo_svg


def test_spacetime_svg_can_hide_sync_messages() -> None:
    sim = get_scenario("late_joiner").run(0)
    with_sync = render_spacetime_svg(sim, include_sync=True)
    without_sync = render_spacetime_svg(sim, include_sync=False)
    ET.fromstring(with_sync)
    ET.fromstring(without_sync)
    assert "sync_req" in with_sync
    assert "sync_req" not in without_sync
