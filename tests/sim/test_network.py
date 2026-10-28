from fieldsync.sim import Simulation


def sim(**kw: object) -> Simulation:
    return Simulation(("A", "B"), anti_entropy_interval=None, **kw)  # type: ignore[arg-type]


def test_messages_arrive_after_latency() -> None:
    s = sim(latency=1.0, jitter=0.0)
    s.edit("A", 0, insert="x")
    s.advance(0.5)
    assert s["B"].text() == ""
    s.advance(0.6)
    assert s["B"].text() == "x"
    (msg,) = s.messages()
    assert msg.status == "delivered" and msg.ended_at == 1.0


def test_fifo_links_preserve_order_despite_jitter() -> None:
    s = sim(latency=1.0, jitter=0.9, seed=3)
    for i in range(10):
        s.edit("A", i, insert=str(i))
        s.advance(0.01)
    s.advance(5)
    arrivals = [m.ended_at for m in s.messages()]
    assert arrivals == sorted(arrivals)
    assert not any(e.kind == "buffer" for e in s.trace)


def test_reorder_causes_buffering_but_still_converges() -> None:
    s = sim(latency=1.0, jitter=0.5, seed=1)
    s.set_reorder(True)
    for i in range(10):
        s.edit("A", i, insert=str(i))
    s.advance(10)
    assert s["B"].text() == "0123456789"
    assert any(e.kind == "buffer" for e in s.trace)


def test_full_loss_drops_everything() -> None:
    s = sim()
    s.set_drop_rate(1.0)
    s.edit("A", 0, insert="x")
    s.advance(5)
    (msg,) = s.messages()
    assert msg.status == "dropped" and msg.drop_reason == "lost"
    assert s["B"].text() == ""


def test_partition_drops_cross_group_messages() -> None:
    s = Simulation(("A", "B", "C"), anti_entropy_interval=None)
    s.partition([["A", "B"], ["C"]])
    s.edit("A", 0, insert="x")
    s.advance(5)
    assert s.texts() == {"A": "x", "B": "x", "C": ""}
    dropped = [m for m in s.messages() if m.status == "dropped"]
    assert [(m.dst, m.drop_reason) for m in dropped] == [("C", "partitioned")]


def test_hold_mode_manual_delivery_in_any_order() -> None:
    s = sim()
    s.set_hold(True)
    s.edit("A", 0, insert="abc")
    s.edit("A", 1, delete=1)
    held = s.network.held()
    assert len(held) == 2
    s.advance(10)  # time passes, nothing moves
    assert s["B"].text() == ""
    s.deliver(held[1].id)  # delete first
    assert len(s["B"].pending) == 1
    s.deliver(held[0].id)
    assert s["B"].text() == "ac" and not s["B"].pending


def test_releasing_hold_schedules_messages() -> None:
    s = sim()
    s.set_hold(True)
    s.edit("A", 0, insert="x")
    s.set_hold(False)
    s.advance(5)
    assert s["B"].text() == "x"


def test_presenter_drop() -> None:
    s = sim()
    s.set_hold(True)
    s.edit("A", 0, insert="x")
    (msg,) = s.network.held()
    s.drop(msg.id)
    assert msg.status == "dropped" and msg.drop_reason == "dropped by presenter"
