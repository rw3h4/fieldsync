"""The load generator end to end: real relay processes, 2 clients, about 2 s of load."""

import time

import pytest

from fieldsync.eval.loadgen import SMOKE, RelayPair, run_test, write_runs


def test_loadgen_smoke_run(tmp_path) -> None:
    start = time.monotonic()
    result = run_test(SMOKE, seed=1, raw_dir=tmp_path / "raw", timeout=15)
    assert time.monotonic() - start < 15
    assert result.changes > 0
    assert result.success_pct == 100.0
    assert result.converged
    assert result.failovers == 0
    assert result.lat_p50_ms > 0
    assert (tmp_path / "raw" / "smoke_1_changes.csv").exists()

    write_runs(tmp_path / "runs.csv", [result])
    write_runs(tmp_path / "runs.csv", [result])  # upsert: still one row
    assert len((tmp_path / "runs.csv").read_text().splitlines()) == 2


def test_relays_are_killed_even_when_the_run_crashes(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="boom"), RelayPair(tmp_path) as relays:
        procs = list(relays.procs.values())
        assert all(p.poll() is None for p in procs)  # both running
        raise RuntimeError("boom")
    assert all(p.poll() is not None for p in procs)
