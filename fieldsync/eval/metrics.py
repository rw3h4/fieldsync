"""Metrics for the M4 evaluation, computed after a run from wall-clock trace events (D26, D41).

A run produces two kinds of records:

- ``Edit(dot, origin, t)``: a change made locally on node ``origin`` at time ``t``
  (the replica's ``edit`` trace event);
- an apply time per ``(dot, node)``: when that change was applied on another node
  (its ``apply`` trace event).

All times are wall-clock seconds from ``time.time()``. Every process runs on one
laptop, so they share a clock and differences across nodes are meaningful.

Definitions (D41):

- **Sync latency** of a change on node ``n`` = apply time on ``n`` - edit time on
  the origin, for every node other than the origin.
- **Success** = fraction of those (change, node) pairs applied by the end of the run.
- **Backlog cleared** after an event at time ``T`` (relay kill, reconnect) = the
  time from ``T`` until every change made at or before ``T`` is applied on every
  node. Edits keep flowing during F1/F2, so the replicas are never all equal at
  one instant; this is the convergence measure that still means something under
  load.
"""

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

Applies = Mapping[tuple[str, str], float]  # (dot, node) -> time of the first apply


@dataclass(frozen=True, slots=True)
class Edit:
    dot: str  # "origin#seq"
    origin: str
    t: float


def pair_latencies(
    edits: Iterable[Edit], applies: Applies, nodes: Sequence[str]
) -> tuple[list[float], int]:
    """Latencies (s) of all applied (change, other node) pairs, and how many pairs were expected."""
    latencies: list[float] = []
    expected = 0
    for edit in edits:
        for node in nodes:
            if node == edit.origin:
                continue
            expected += 1
            t = applies.get((edit.dot, node))
            if t is not None:
                latencies.append(t - edit.t)
    return latencies, expected


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-th percentile (0-100) with linear interpolation; NaN for no values."""
    if not values:
        return math.nan
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def backlog_cleared(
    edits: Iterable[Edit], applies: Applies, nodes: Sequence[str], event_t: float
) -> float | None:
    """Seconds from ``event_t`` until every change made by then is applied on every node.

    ``None`` if some such change never reached some node (not recovered).
    """
    last = event_t
    for edit in edits:
        if edit.t > event_t:
            continue
        for node in nodes:
            if node == edit.origin:
                continue
            t = applies.get((edit.dot, node))
            if t is None:
                return None
            last = max(last, t)
    return last - event_t


@dataclass(frozen=True, slots=True)
class Bucket:
    start: float  # seconds after t0 (bucket by the edit's time)
    changes: int
    median_ms: float
    max_ms: float


def latency_timeline(
    edits: Iterable[Edit],
    applies: Applies,
    nodes: Sequence[str],
    t0: float,
    bucket: float = 1.0,
) -> list[Bucket]:
    """Median and max sync latency of the changes made in each ``bucket``-second slot."""
    by_slot: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    for edit in edits:
        slot = math.floor((edit.t - t0) / bucket)
        counts[slot] = counts.get(slot, 0) + 1
        lats, _ = pair_latencies([edit], applies, nodes)
        by_slot.setdefault(slot, []).extend(lats)
    out = []
    for slot in sorted(counts):
        lats = by_slot.get(slot, [])
        out.append(
            Bucket(
                start=slot * bucket,
                changes=counts[slot],
                median_ms=statistics.median(lats) * 1000 if lats else math.nan,
                max_ms=max(lats) * 1000 if lats else math.nan,
            )
        )
    return out
