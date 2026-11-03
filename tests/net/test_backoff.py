from fieldsync.net.client import Backoff


def test_backoff_doubles_up_to_the_cap_and_resets() -> None:
    b = Backoff(initial=0.5, cap=3.0, jitter=0.0)
    assert [b.next() for _ in range(5)] == [0.5, 1.0, 2.0, 3.0, 3.0]
    b.reset()
    assert b.next() == 0.5


def test_backoff_jitter_stays_within_bounds() -> None:
    lo = Backoff(initial=1.0, jitter=0.1, rng=lambda: 0.0)
    hi = Backoff(initial=1.0, jitter=0.1, rng=lambda: 1.0)
    assert lo.next() == 0.9 and hi.next() == 1.1
