from fieldsync.eval.loadgen import RUN_COLUMNS
from fieldsync.eval.report import render


def row(test: str, run: int, **values: str) -> dict[str, str]:
    base = dict.fromkeys(RUN_COLUMNS, "")
    base.update(
        test=test,
        run=str(run),
        changes="100",
        lat_p50_ms="2.000",
        lat_p95_ms="4.000",
        success_pct="100.000",
        converged="True",
        failovers="0",
        loop_lag_p95_ms="1.0",
        harness_limited="False",
    )
    base.update(values)
    return base


def test_summary_aggregates_runs_and_flags_harness_limited() -> None:
    rows = [
        row("P1", 1),
        row("P1", 2, lat_p50_ms="4.000"),
        row("P4", 1, harness_limited="True", loop_lag_p95_ms="80.0"),
    ]
    md = render(rows, {"CPU": "test cpu"})
    assert "| CPU | test cpu |" in md
    assert "| **all** | 100 (100-100) |" in md  # changes: mean (min-max)
    assert "3.0 (2.0-4.0)" in md  # P1 p50 over two runs
    assert "harness-limited |" in md and "| 1 |" in md
    assert "## P4: 16 clients x 10 edits/s" in md
