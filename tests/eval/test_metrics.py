import math

from fieldsync.eval.metrics import (
    Edit,
    backlog_cleared,
    latency_timeline,
    pair_latencies,
    percentile,
)

NODES = ["a", "b", "c"]
EDITS = [Edit("a#1", "a", 10.0), Edit("b#1", "b", 11.0)]
APPLIES = {
    ("a#1", "b"): 10.1,
    ("a#1", "c"): 10.3,
    ("b#1", "a"): 11.2,
    # ("b#1", "c") never applied
}


def test_pair_latencies_skip_the_origin_and_count_missing_pairs() -> None:
    lats, expected = pair_latencies(EDITS, APPLIES, NODES)
    assert expected == 4  # 2 changes x 2 other nodes
    assert sorted(round(x, 3) for x in lats) == [0.1, 0.2, 0.3]


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([0, 10], 95) == 9.5
    assert math.isnan(percentile([], 50))


def test_backlog_cleared_counts_only_changes_made_before_the_event() -> None:
    assert round(backlog_cleared(EDITS, APPLIES, NODES, event_t=10.5), 3) == 0.0  # a#1 done by 10.3
    assert backlog_cleared(EDITS, APPLIES, NODES, event_t=11.0) is None  # b#1 never reached c
    done = {**APPLIES, ("b#1", "c"): 14.0}
    assert backlog_cleared(EDITS, done, NODES, event_t=11.0) == 3.0


def test_latency_timeline_buckets_by_edit_time() -> None:
    (first, second) = latency_timeline(EDITS, APPLIES, NODES, t0=10.0)
    assert (first.start, first.changes, round(first.median_ms), round(first.max_ms)) == (
        0.0,
        1,
        200,
        300,
    )
    assert (second.start, second.changes, round(second.max_ms)) == (1.0, 1, 200)
