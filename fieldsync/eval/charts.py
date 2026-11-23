"""``uv run --group eval loadgen-charts``: the M4 charts and derived tables.

Reads ``results/runs.csv``, ``results/timelines.csv`` and the per-change CSVs in
``results/raw/``, and writes:

- ``results/charts/latency_vs_clients.png``
- ``results/charts/throughput_vs_offered.png``
- ``results/charts/f1_timeline.png`` (F1 kill and F1b hang, the fault marked)
- ``results/charts/f2_convergence.png`` (unapplied change/node pairs over time)
- ``results/rate_gap.csv``: achieved vs offered load, with the mean gap between
  a client's edits against the scheduled 1/r (where the shortfall comes from)
- ``results/f2_backlog.csv``: the data behind the F2 chart

matplotlib is in the "eval" dependency group, so only this module needs it.
"""

import argparse
import csv
import itertools
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fieldsync.eval.loadgen import TESTS

# Reference palette (light mode), categorical slots in fixed order.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK_2, GRID, SURFACE, BAND = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#f0efec"
P_TESTS = ["P1", "P2", "P3", "P4-mp"]


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK_2,
            "axes.titlecolor": INK,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": INK_2,
            "ytick.color": INK_2,
            "legend.frameon": False,
            "legend.labelcolor": INK,
            "lines.linewidth": 2,
            "lines.markersize": 8,
            "font.size": 10,
        }
    )


# ================================================================== inputs
def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _by_test(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        out[r["test"]].append(r)
    return out


def _vals(rows: Sequence[dict[str, str]], key: str) -> list[float]:
    return [float(r[key]) for r in rows if r.get(key) not in (None, "", "nan")]


def _changes(raw: Path, test: str, run: int) -> list[tuple[str, str, float]]:
    return [
        (r["dot"], r["origin"], float(r["t_edit"]))
        for r in _read(raw / f"{test}_{run}_changes.csv")
    ]


def _applies(raw: Path, test: str, run: int) -> dict[tuple[str, str], float]:
    return {
        (r["dot"], r["node"]): float(r["t_apply"]) for r in _read(raw / f"{test}_{run}_applies.csv")
    }


# =============================================================== rate gap
def rate_gap(raw: Path, tests: Sequence[str]) -> list[dict[str, str]]:
    """Achieved vs offered over all runs, and the mean gap between a client's edits.

    Each client sleeps an exponential gap with mean 1/r, then edits. If the
    observed mean gap matches 1/r, a shortfall is Poisson noise (z = how many
    standard deviations of a Poisson count it is). If the gap is longer, the
    loop woke the client late: harness scheduling delay.
    """
    out = []
    for test in tests:
        spec = TESTS[test]
        offered = achieved = 0.0
        gaps: list[float] = []
        for run in (1, 2, 3):
            by_client: dict[str, list[float]] = defaultdict(list)
            for _, origin, t in _changes(raw, test, run):
                by_client[origin].append(t)
            achieved += sum(0 <= t < spec.window for ts in by_client.values() for t in ts)
            offered += spec.clients * spec.rate * spec.window
            for ts in by_client.values():
                ts.sort()
                # skip the first second of warm-up (client start-up)
                gaps += [b - a for a, b in itertools.pairwise(ts) if a >= 1 - spec.warmup]
        mean_gap = statistics.mean(gaps)
        scheduled = 1 / spec.rate
        out.append(
            {
                "test": test,
                "offered_changes": f"{offered:.0f}",
                "achieved_changes": f"{achieved:.0f}",
                "short_pct": f"{100 * (1 - achieved / offered):.2f}",
                "poisson_z": f"{(achieved - offered) / math.sqrt(offered):+.1f}",
                "scheduled_gap_ms": f"{1000 * scheduled:.1f}",
                "mean_gap_ms": f"{1000 * mean_gap:.2f}",
                "gap_overshoot_pct": f"{100 * (mean_gap / scheduled - 1):+.2f}",
                "gap_se_ms": f"{1000 * statistics.stdev(gaps) / math.sqrt(len(gaps)):.2f}",
                "gaps": str(len(gaps)),
            }
        )
    return out


# ============================================================ F2 backlog
def backlog_series(raw: Path, test: str, run: int, clients: int, step: float = 0.1):
    """(t, unapplied pairs): change/node pairs made by time t but not yet applied there."""
    events: list[tuple[float, int]] = []
    applies = _applies(raw, test, run)
    # +1 per other node when a change is made, -1 when it is applied on a node.
    # Only edits count: the record creates are applied too but are not edits.
    edits = _changes(raw, test, run)
    dots = {dot for dot, _, _ in edits}
    events.extend((t_edit, clients - 1) for _, _, t_edit in edits)
    events.extend((t, -1) for (dot, _node), t in applies.items() if dot in dots)
    events.sort()
    out, level, i = [], 0, 0
    t, t_last = -2.0, events[-1][0] + 1
    while t <= t_last:
        while i < len(events) and events[i][0] <= t:
            level += events[i][1]
            i += 1
        out.append((t, level))
        t += step
    return out


# ================================================================= charts
def latency_vs_clients(runs: dict[str, list[dict[str, str]]], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for key, color, name in (("lat_p50_ms", BLUE, "median (p50)"), ("lat_p95_ms", ORANGE, "p95")):
        xs, ys, lo, hi = [], [], [], []
        for test in P_TESTS:
            v = _vals(runs[test], key)
            xs.append(TESTS[test].clients)
            ys.append(statistics.mean(v))
            lo.append(statistics.mean(v) - min(v))
            hi.append(max(v) - statistics.mean(v))
        ax.errorbar(xs, ys, yerr=[lo, hi], color=color, marker="o", capsize=4, label=name)
        ax.annotate(name, (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points", va="center")
        # Single-process P4: harness-limited, shown hollow and not joined to the line.
        p4 = _vals(runs["P4"], key)
        ax.plot(
            [16],
            [statistics.mean(p4)],
            marker="o",
            markerfacecolor=SURFACE,
            markeredgecolor=color,
            markeredgewidth=2,
            linestyle="none",
        )
    ax.annotate(
        "P4, one loadgen process\n(harness-limited, hollow)",
        (16, statistics.mean(_vals(runs["P4"], "lat_p95_ms"))),
        xytext=(-10, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        color=INK_2,
        fontsize=9,
    )
    ax.set_xscale("log", base=2)
    ax.set_xticks([2, 4, 8, 16], ["2\nP1", "4\nP2", "8\nP3", "16\nP4-mp"])
    ax.set_yscale("log")
    ax.set_xlim(1.6, 24)
    ax.set_xlabel("clients")
    ax.set_ylabel("sync latency, ms (log scale)")
    ax.set_title("Sync latency vs clients (mean of 3 runs; bars: min-max)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def throughput_vs_offered(runs: dict[str, list[dict[str, str]]], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    top = 170
    ax.plot([0, top], [0, top], color=INK_2, linewidth=1, linestyle="--")
    ax.annotate(
        "achieved = offered",
        (top * 0.55, top * 0.55),
        xytext=(4, -14),
        textcoords="offset points",
        color=INK_2,
        fontsize=9,
        rotation=0,
    )
    for test, filled in (*((t, True) for t in P_TESTS), ("P4", False)):
        rs = runs[test]
        offered = TESTS[test].clients * TESTS[test].rate
        achieved = statistics.mean(_vals(rs, "throughput_cps"))
        ax.plot(
            [offered],
            [achieved],
            marker="o",
            color=BLUE,
            markerfacecolor=BLUE if filled else SURFACE,
            markeredgewidth=2,
            linestyle="none",
        )
        label = test if filled else "P4 (one process,\nharness-limited)"
        ax.annotate(
            f"{label}: {achieved:.1f}/s",
            (offered, achieved),
            xytext=(-8, 6) if filled else (-8, -4),
            textcoords="offset points",
            ha="right",
            va="bottom" if filled else "top",
            color=INK,
            fontsize=9,
        )
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_xlabel("offered load, changes/s (clients x rate)")
    ax.set_ylabel("achieved, changes originated/s")
    ax.set_title("Throughput vs offered load (mean of 3 runs)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fault_timelines(
    runs: dict[str, list[dict[str, str]]], timelines: list[dict[str, str]], path: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    titles = {"F1": "F1: relay A killed", "F1b": "F1b: relay A hung (SIGSTOP)"}
    for ax, test in zip(axes, ("F1", "F1b"), strict=True):
        buckets: dict[float, list[dict[str, str]]] = defaultdict(list)
        for r in timelines:
            if r["test"] == test:
                buckets[float(r["start_s"])].append(r)
        xs = sorted(buckets)
        med = [statistics.median(float(r["median_ms"]) for r in buckets[x]) for x in xs]
        mx = [max(float(r["max_ms"]) for r in buckets[x]) for x in xs]
        ax.plot([x + 0.5 for x in xs], mx, color=ORANGE, marker="o", markersize=4, label="max")
        ax.plot([x + 0.5 for x in xs], med, color=BLUE, marker="o", markersize=4, label="median")
        fault = statistics.mean(_vals(runs[test], "fault_at_s"))
        restored = statistics.mean(_vals(runs[test], "restored_s"))
        ax.axvline(fault, color=INK, linewidth=1)
        ax.annotate(
            f"fault at {fault:.0f} s\nrestored after {restored:.2f} s",
            (fault, 0.5),
            xytext=(80, 0),
            textcoords="offset points",
            color=INK,
            fontsize=9,
            va="center",
            xycoords=("data", "axes fraction"),
        )
        ax.set_yscale("log")
        ax.set_xlabel("time of the edit, s into the window")
        ax.set_title(titles[test])
        ax.annotate(
            "max",
            (xs[-1] + 0.5, mx[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            color=INK,
            fontsize=9,
        )
        ax.annotate(
            "median",
            (xs[-1] + 0.5, med[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            color=INK,
            fontsize=9,
        )
    axes[0].set_ylabel("sync latency per 1 s bucket, ms (log)")
    axes[0].legend(loc="upper left", bbox_to_anchor=(0, 0.9))
    fig.suptitle(
        "Latency around a relay failure, P2 load (median / max over 3 runs)",
        x=0.01,
        ha="left",
        fontweight="bold",
        color=INK,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def f2_convergence(raw: Path, runs: dict[str, list[dict[str, str]]], path: Path) -> list[dict]:
    spec = TESTS["F2"]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.axvspan(spec.fault_at, spec.fault_at + spec.fault_len, color=BAND, zorder=0)
    ax.annotate(
        "2 of 4 clients offline",
        (spec.fault_at + spec.fault_len / 2, 1),
        xycoords=("data", "axes fraction"),
        xytext=(0, -14),
        textcoords="offset points",
        ha="center",
        color=INK_2,
        fontsize=9,
    )
    rows = []
    for run, color in zip((1, 2, 3), (BLUE, ORANGE, AQUA), strict=True):
        series = backlog_series(raw, "F2", run, spec.clients)
        ax.plot([t for t, _ in series], [n for _, n in series], color=color, label=f"run {run}")
        rows += [{"run": run, "t_s": f"{t:.1f}", "unapplied_pairs": n} for t, n in series]
    reconnect = spec.fault_at + spec.fault_len
    cleared = statistics.mean(_vals(runs["F2"], "recovery_s"))
    ax.annotate(
        f"reconnect: backlog\ncleared in {cleared:.2f} s",
        (reconnect, 0),
        xytext=(10, 40),
        textcoords="offset points",
        color=INK,
        fontsize=9,
        arrowprops={"arrowstyle": "-", "color": INK_2, "linewidth": 1},
    )
    ax.set_xlim(-2, spec.window + 3)
    ax.set_xlabel("s into the window")
    ax.set_ylabel("change/node pairs not yet applied")
    ax.set_title("F2: backlog of unapplied changes during a 30 s outage of half the clients")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loadgen-charts", description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    res, raw, charts = args.results, args.results / "raw", args.results / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    _style()
    runs = _by_test(_read(res / "runs.csv"))
    latency_vs_clients(runs, charts / "latency_vs_clients.png")
    throughput_vs_offered(runs, charts / "throughput_vs_offered.png")
    fault_timelines(runs, _read(res / "timelines.csv"), charts / "f1_timeline.png")
    _write(res / "f2_backlog.csv", f2_convergence(raw, runs, charts / "f2_convergence.png"))
    _write(res / "rate_gap.csv", rate_gap(raw, ["P1", "P2", "P3", "P4", "P4-mp"]))
    print(f"wrote {charts}/*.png, {res}/rate_gap.csv, {res}/f2_backlog.csv")


if __name__ == "__main__":
    main()
