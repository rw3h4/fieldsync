from editor.sim.scheduler import Scheduler


def test_events_run_in_time_then_fifo_order() -> None:
    s = Scheduler()
    out: list[str] = []
    s.schedule(2.0, lambda: out.append("late"))
    s.schedule(1.0, lambda: out.append("first"))
    s.schedule(1.0, lambda: out.append("second"))
    s.run_until(1.5)
    assert out == ["first", "second"] and s.now == 1.5
    s.run_until(10)
    assert out[-1] == "late"


def test_foreground_tracking() -> None:
    s = Scheduler()
    s.schedule(1.0, lambda: None, background=True)
    assert not s.has_foreground
    s.schedule(1.0, lambda: None)
    assert s.has_foreground
    s.run_until(2)
    assert not s.has_foreground


def test_seeded_rng_is_reproducible() -> None:
    assert Scheduler(7).rng.random() == Scheduler(7).rng.random()
