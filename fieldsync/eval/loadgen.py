"""``uv run loadgen``: the M4 load generator (task e, D40).

One run of one test:

1. Start two **relays as real child processes** (``python -m fieldsync.net.relay``),
   peered, each with its own ``--log-file`` in a temporary directory. F1 kills
   relay A for real. The relays are always killed in a ``finally`` (also on
   Ctrl+C), so a crashed run never leaves relays holding ports.
2. Start N headless ``Node`` clients in this process, on one asyncio loop, each
   with its own data file in the same temporary directory and ``relays=[A, B]``.
   Persistence is part of the system's fault tolerance, so its cost is measured.
3. Client 0 creates 4 shared records; wait until every node has them.
4. Seeded workload: each client edits at rate r (exponential gaps, mean 1/r):
   80% insert 1-3 letters at a random position, 20% delete 1-2 characters.
   Warm-up, then the measurement window, during which F1/F1b/F2 inject their
   fault. F1 kills relay A; F1b hangs it with SIGSTOP (POSIX only, D42) so only
   the missing heartbeats reveal it; F2 takes half the clients offline.
5. Stop editing, wait (up to ``drain`` s) until all replicas converge, stop the
   nodes and relays, delete the temporary directory.
6. Compute the metrics (``fieldsync.eval.metrics``, D41) from the trace events
   and write them: per-change CSVs to ``results/raw/``, one row per run to
   ``results/runs.csv``.

The loadgen loop's own lag is sampled every 100 ms. If its p95 in the window is
above 50 ms, the run is marked ``harness_limited``: the clients could not keep
up, so the limit belongs to the harness, not to FieldSync (D41).
"""

import argparse
import asyncio
import contextlib
import csv
import logging
import random
import re
import signal
import socket
import string
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from fieldsync.eval.metrics import (
    Bucket,
    Edit,
    backlog_cleared,
    latency_timeline,
    pair_latencies,
    percentile,
)
from fieldsync.net.node import Node
from fieldsync.runtime import TraceEvent

log = logging.getLogger(__name__)

RECORDS = 4
DELETE_SHARE = 0.2
LAG_SAMPLE = 0.1  # seconds between event-loop lag samples
HARNESS_LAG_LIMIT_MS = 50.0  # D41: p95 loop lag above this => run is harness-limited
_CHANGE_SUMMARY = re.compile(r"^\S+#\d+ \[")  # how a ChangeMsg shows in a receive event


@dataclass(frozen=True)
class Spec:
    """One test from PROJECT_BRIEF.md M4."""

    name: str
    clients: int
    rate: float  # edits per second per client
    warmup: float = 5.0
    window: float = 30.0
    drain: float = 15.0  # max wait for convergence after the window
    fault: str | None = None  # "kill_relay", "hang_relay" or "offline_half"
    fault_at: float = 0.0  # seconds into the window
    fault_len: float = 0.0  # offline duration (F2)
    workers: int = 1  # loadgen processes hosting the clients (P4-mp, D43)


TESTS: dict[str, Spec] = {
    "P1": Spec("P1", clients=2, rate=1.0),
    "P2": Spec("P2", clients=4, rate=5.0),
    "P3": Spec("P3", clients=8, rate=5.0),
    "P4": Spec("P4", clients=16, rate=10.0),
    "P4-mp": Spec("P4-mp", clients=16, rate=10.0, drain=10.0, workers=4),
    "F1": Spec("F1", clients=4, rate=5.0, fault="kill_relay", fault_at=15.0),
    "F1b": Spec("F1b", clients=4, rate=5.0, fault="hang_relay", fault_at=15.0),
    "F2": Spec(
        "F2", clients=4, rate=5.0, window=50.0, fault="offline_half", fault_at=10.0, fault_len=30.0
    ),
}
# SIGSTOP/SIGCONT exist only on POSIX; F1b is skipped elsewhere (Windows).
CAN_HANG = hasattr(signal, "SIGSTOP")
SMOKE = Spec("smoke", clients=2, rate=5.0, warmup=0.5, window=2.0, drain=5.0)


@dataclass
class RunResult:
    """One row of results/runs.csv. Times in seconds unless the name says ms."""

    test: str
    run: int
    seed: int
    clients: int
    rate_per_client: float
    offered_cps: float
    window_s: float
    changes: int
    throughput_cps: float
    fwd_msgs_ps: float
    lat_p50_ms: float
    lat_p95_ms: float
    lat_max_ms: float
    pairs_expected: int
    pairs_applied: int
    success_pct: float
    converged: bool
    failovers: int
    fault_at_s: float | None
    failover_s: float | None
    recovery_s: float | None  # backlog cleared: changes made up to the event (D41)
    loop_lag_p95_ms: float
    harness_limited: bool
    # F1/F1b: fault -> every change made up to failover-complete is applied
    # everywhere, i.e. including the changes made while the fault was undetected.
    restored_s: float | None = None
    workers: int = 1  # loadgen processes; a run is harness-limited if any worker's p95 > 50 ms
    # F2 only: latency split by who edited and who received (D42). "on" = the
    # clients that stayed online; "pre" = before the outage, "outage" = during it.
    f2_on_on_pre_p50_ms: float | None = None
    f2_on_on_pre_p95_ms: float | None = None
    f2_on_on_outage_p50_ms: float | None = None
    f2_on_on_outage_p95_ms: float | None = None
    f2_offline_origin_p50_ms: float | None = None
    f2_offline_origin_p95_ms: float | None = None
    # Fault tests: 1 s latency timeline (written to timelines.csv, not runs.csv).
    timeline: list[Bucket] = field(default_factory=list, repr=False, metadata={"csv": False})


@dataclass
class Trace:
    """What the nodes' trace observers collect during a run."""

    edits: list[Edit] = field(default_factory=list)
    applies: dict[tuple[str, str], float] = field(default_factory=dict)
    change_receives: list[float] = field(default_factory=list)  # times a node got a ChangeMsg

    def observer(self, node_id: str):
        def observe(event: TraceEvent) -> None:
            if event.kind == "edit" and event.dot is not None:
                self.edits.append(Edit(_dot(event.dot), node_id, event.time))
            elif event.kind == "apply" and event.dot is not None:
                self.applies.setdefault((_dot(event.dot), node_id), event.time)
            elif event.kind == "receive" and _CHANGE_SUMMARY.match(event.detail):
                self.change_receives.append(event.time)

        return observe


def _dot(dot: tuple[str, int]) -> str:
    return f"{dot[0]}#{dot[1]}"


# ================================================================= relays
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RelayPair:
    """relay_a and relay_b as child processes, peered with each other.

    Use as a context manager: on exit (normal, exception or Ctrl+C) every relay
    still running is killed and waited for.
    """

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.ports = {"a": _free_port(), "b": _free_port()}
        self.procs: dict[str, subprocess.Popen[bytes]] = {}

    def url(self, name: str) -> str:
        return f"ws://127.0.0.1:{self.ports[name]}"

    def __enter__(self) -> "RelayPair":
        try:
            for name, other in (("a", "b"), ("b", "a")):
                out = (self.workdir / f"relay_{name}.out").open("wb")
                self.procs[name] = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "fieldsync.net.relay",
                        "--id",
                        f"relay_{name}",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self.ports[name]),
                        "--peer",
                        self.url(other),
                        "--log-file",
                        str(self.workdir / f"relay_{name}.jsonl"),
                    ],
                    stdout=out,
                    stderr=subprocess.STDOUT,
                )
                out.close()  # the child has its own handle
            for name in self.procs:
                self._wait_listening(name)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _wait_listening(self, name: str, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.procs[name].poll() is not None:
                raise RuntimeError(f"relay_{name} exited: {self._output(name)}")
            with (
                contextlib.suppress(OSError),
                socket.create_connection(("127.0.0.1", self.ports[name]), timeout=0.2),
            ):
                return
            time.sleep(0.05)
        raise RuntimeError(f"relay_{name} did not start listening: {self._output(name)}")

    def _output(self, name: str) -> str:
        path = self.workdir / f"relay_{name}.out"
        return path.read_text(errors="replace")[-2000:] if path.exists() else ""

    def kill(self, name: str) -> None:
        proc = self.procs[name]
        proc.kill()
        proc.wait(timeout=10)

    def hang(self, name: str) -> None:
        """Freeze the relay (SIGSTOP): its sockets stay open, it just stops answering."""
        self.procs[name].send_signal(signal.SIGSTOP)

    def resume(self, name: str) -> None:
        self.procs[name].send_signal(signal.SIGCONT)

    def close(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
                if CAN_HANG:
                    proc.send_signal(signal.SIGCONT)  # in case it was hung
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)


# ============================================================== workload
def random_edit(node: Node, records: Sequence[str], rng: random.Random) -> None:
    """One workload edit: mostly short inserts at random positions, some deletes."""
    record = rng.choice(records)
    n = len(node.replica.text(record))
    if n and rng.random() < DELETE_SHARE:
        index = rng.randrange(n)
        node.replica.edit(record, index, delete=min(rng.randint(1, 2), n - index))
    else:
        text = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 3)))
        node.replica.edit(record, rng.randint(0, n), insert=text)


async def _client(node: Node, records: Sequence[str], rng: random.Random, rate: float) -> None:
    while True:
        await asyncio.sleep(rng.expovariate(rate))
        random_edit(node, records, rng)


async def _lag_monitor(samples: list[tuple[float, float]]) -> None:
    """How late the loop wakes us up: a direct measure of harness saturation."""
    while True:
        expected = time.monotonic() + LAG_SAMPLE
        await asyncio.sleep(LAG_SAMPLE)
        samples.append((time.time(), time.monotonic() - expected))


async def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


def _converged(nodes: Sequence[Node]) -> bool:
    return (
        len({n.replica.clock for n in nodes}) == 1
        and not any(n.replica.pending for n in nodes)
        and len({n.replica.state_digest() for n in nodes}) == 1
    )


# =================================================================== run
async def _run(
    spec: Spec, seed: int, run: int, relays: RelayPair, workdir: Path, raw_dir: Path | None
) -> RunResult:
    trace = Trace()
    urls = [relays.url("a"), relays.url("b")]
    nodes = [
        Node(f"c{i:02d}", urls, data_file=workdir / f"c{i:02d}.jsonl") for i in range(spec.clients)
    ]
    ids = [n.id for n in nodes]
    tasks: list[asyncio.Task[None]] = []
    lag: list[tuple[float, float]] = []
    fault_t: float | None = None  # the event backlog-cleared is measured from
    outage_t: float | None = None  # F2: when the clients went offline
    failover_s: float | None = None
    try:
        for node in nodes:
            node.replica.observers.append(trace.observer(node.id))
            await node.start()
        if not await _wait_until(lambda: all(n.status.state == "connected" for n in nodes), 15):
            raise RuntimeError("clients did not all connect")
        creator = nodes[0].replica
        records = [creator.create_record(f"record {k}", "case_note") for k in range(RECORDS)]
        if not await _wait_until(
            lambda: all(len(n.replica.list_records()) == RECORDS for n in nodes), 15
        ):
            raise RuntimeError("records did not reach every client")

        tasks.append(asyncio.create_task(_lag_monitor(lag)))
        for i, node in enumerate(nodes):
            rng = random.Random(f"{seed}:{run}:{i}")
            tasks.append(asyncio.create_task(_client(node, records, rng, spec.rate)))

        t0 = time.time() + spec.warmup  # window start
        t_end = t0 + spec.window
        await asyncio.sleep(spec.warmup + spec.fault_at)
        if spec.fault in ("kill_relay", "hang_relay"):
            fault_t = time.time()
            if spec.fault == "kill_relay":
                relays.kill("a")
            else:
                relays.hang("a")
            b = relays.url("b")
            if await _wait_until(
                lambda: all(n.status.state == "connected" and n.status.url == b for n in nodes),
                spec.window - spec.fault_at,
            ):
                failover_s = time.time() - fault_t
        elif spec.fault == "offline_half":
            outage_t = time.time()
            half = nodes[: spec.clients // 2]
            for node in half:
                node.set_offline(True)
            await asyncio.sleep(spec.fault_len)
            fault_t = time.time()  # F2 measures from the reconnect
            for node in half:
                node.set_offline(False)
        await asyncio.sleep(max(0.0, t_end - time.time()))
        if spec.fault == "hang_relay":
            relays.resume("a")  # clients stay on relay_b (no flapping back, D20)

        for task in tasks:
            task.cancel()
        converged = await _wait_until(lambda: _converged(nodes), spec.drain)
        failovers = sum(n.status.failovers for n in nodes)
    finally:
        for task in tasks:
            task.cancel()
        for node in nodes:
            with contextlib.suppress(Exception):
                await node.stop()

    if raw_dir is not None:
        _write_raw(raw_dir, spec, run, t0, trace)
    result = base_result(spec, seed, run, trace, ids, t0, window_lag_p95(lag, t0, t_end))
    result.converged, result.failovers = converged, failovers
    window_edits = [e for e in trace.edits if t0 <= e.t < t_end]
    if fault_t is not None:
        result.fault_at_s = fault_t - t0
        result.failover_s = failover_s
        result.recovery_s = backlog_cleared(trace.edits, trace.applies, ids, fault_t)
    if failover_s is not None and fault_t is not None:
        healed_t = fault_t + failover_s
        cleared = backlog_cleared(trace.edits, trace.applies, ids, healed_t)
        result.restored_s = failover_s + cleared if cleared is not None else None
    if fault_t is not None:
        result.timeline = latency_timeline(window_edits, trace.applies, ids, t0)
    if outage_t is not None and fault_t is not None:
        _f2_split(result, window_edits, trace.applies, ids, spec.clients // 2, outage_t, fault_t)
    return result


def window_lag_p95(lag: Sequence[tuple[float, float]], t0: float, t_end: float) -> float:
    """p95 (ms) of the loop-lag samples taken inside the window."""
    return percentile([s * 1000 for t, s in lag if t0 <= t < t_end], 95)


def base_result(
    spec: Spec, seed: int, run: int, trace: Trace, ids: Sequence[str], t0: float, lag_p95: float
) -> RunResult:
    """The metrics every test has (D41); fault fields are filled in by the caller."""
    t_end = t0 + spec.window
    window_edits = [e for e in trace.edits if t0 <= e.t < t_end]
    latencies, expected = pair_latencies(window_edits, trace.applies, ids)
    return RunResult(
        test=spec.name,
        run=run,
        seed=seed,
        clients=spec.clients,
        rate_per_client=spec.rate,
        offered_cps=spec.clients * spec.rate,
        window_s=spec.window,
        changes=len(window_edits),
        throughput_cps=len(window_edits) / spec.window,
        fwd_msgs_ps=sum(t0 <= t < t_end for t in trace.change_receives) / spec.window,
        lat_p50_ms=percentile(latencies, 50) * 1000,
        lat_p95_ms=percentile(latencies, 95) * 1000,
        lat_max_ms=max(latencies) * 1000 if latencies else float("nan"),
        pairs_expected=expected,
        pairs_applied=len(latencies),
        success_pct=100 * len(latencies) / expected if expected else float("nan"),
        converged=False,
        failovers=0,
        fault_at_s=None,
        failover_s=None,
        recovery_s=None,
        loop_lag_p95_ms=lag_p95,
        harness_limited=lag_p95 > HARNESS_LAG_LIMIT_MS,
        workers=spec.workers,
    )


def _f2_split(
    result: RunResult,
    edits: list[Edit],
    applies: dict[tuple[str, str], float],
    ids: list[str],
    n_offline: int,
    outage_t: float,
    reconnect_t: float,
) -> None:
    """F2 latency by group (D42).

    - online -> online, before and during the outage: does the outage slow down
      the clients that stayed connected? (It shouldn't.)
    - offline origin -> every other node: edits made while offline wait for the
      reconnect by design.
    """
    offline, online = ids[:n_offline], ids[n_offline:]

    def p(es: list[Edit], receivers: list[str], q: float) -> float:
        return percentile(pair_latencies(es, applies, receivers)[0], q) * 1000

    on_pre = [e for e in edits if e.origin in online and e.t < outage_t]
    on_outage = [e for e in edits if e.origin in online and outage_t <= e.t < reconnect_t]
    off_origin = [e for e in edits if e.origin in offline]
    result.f2_on_on_pre_p50_ms = p(on_pre, online, 50)
    result.f2_on_on_pre_p95_ms = p(on_pre, online, 95)
    result.f2_on_on_outage_p50_ms = p(on_outage, online, 50)
    result.f2_on_on_outage_p95_ms = p(on_outage, online, 95)
    result.f2_offline_origin_p50_ms = p(off_origin, ids, 50)
    result.f2_offline_origin_p95_ms = p(off_origin, ids, 95)


def run_test(
    spec: Spec, *, seed: int = 1, run: int = 1, raw_dir: Path | None = None, timeout: float = 300.0
) -> RunResult:
    """Run one test once, from a fresh temporary directory and fresh relays."""
    if spec.workers > 1:
        from fieldsync.eval.multiproc import run_multi  # here: avoids an import cycle

        return run_multi(spec, seed=seed, run=run, raw_dir=raw_dir, timeout=timeout)
    with (
        tempfile.TemporaryDirectory(prefix="fieldsync-loadgen-", ignore_cleanup_errors=True) as tmp,
        RelayPair(Path(tmp)) as relays,
    ):

        async def bounded() -> RunResult:
            run_once = _run(spec, seed, run, relays, Path(tmp), raw_dir)
            return await asyncio.wait_for(run_once, timeout)

        return asyncio.run(bounded())


# ================================================================ output
def _write_raw(raw_dir: Path, spec: Spec, run: int, t0: float, trace: Trace) -> None:
    """Per-change CSVs; times are seconds relative to the window start."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / f"{spec.name}_{run}_changes.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dot", "origin", "t_edit"])
        for e in trace.edits:
            w.writerow([e.dot, e.origin, f"{e.t - t0:.6f}"])
    with (raw_dir / f"{spec.name}_{run}_applies.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dot", "node", "t_apply"])
        for (dot, node), t in trace.applies.items():
            w.writerow([dot, node, f"{t - t0:.6f}"])


RUN_COLUMNS = [f.name for f in fields(RunResult) if f.metadata.get("csv", True)]
TIMELINE_COLUMNS = ["test", "run", "start_s", "changes", "median_ms", "max_ms"]


def _upsert(path: Path, columns: list[str], key: list[str], new: list[dict[str, str]]) -> None:
    """Rewrite ``path`` with ``new`` rows replacing old rows that have the same ``key``."""
    rows: list[dict[str, str]] = []
    replaced = {tuple(r[k] for k in key[:2]) for r in new}  # (test, run)
    if path.exists():
        with path.open(newline="") as f:
            rows = [r for r in csv.DictReader(f) if (r["test"], r["run"]) not in replaced]
    rows.extend(new)
    rows.sort(key=lambda r: tuple(_sort_key(r[k]) for k in key))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def write_runs(path: Path, results: Sequence[RunResult]) -> None:
    """Upsert rows into runs.csv, keyed by (test, run): re-running replaces old rows."""
    new = [{k: _fmt(v) for k, v in asdict(r).items() if k in RUN_COLUMNS} for r in results]
    _upsert(path, RUN_COLUMNS, ["test", "run"], new)


def write_timelines(path: Path, results: Sequence[RunResult]) -> None:
    """Upsert the fault tests' 1 s latency timelines (seconds relative to the window start)."""
    new = [
        {
            "test": r.test,
            "run": str(r.run),
            "start_s": _fmt(b.start),
            "changes": str(b.changes),
            "median_ms": _fmt(b.median_ms),
            "max_ms": _fmt(b.max_ms),
        }
        for r in results
        for b in r.timeline
    ]
    if new:
        _upsert(path, TIMELINE_COLUMNS, ["test", "run", "start_s"], new)


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loadgen", description="FieldSync M4 load generator")
    parser.add_argument("--test", action="append", choices=sorted(TESTS), help="repeatable")
    parser.add_argument("--all", action="store_true", help="P1-P4, F1, F2")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    names = list(TESTS) if args.all else (args.test or parser.error("give --test or --all"))
    if "F1b" in names and not CAN_HANG:
        print("F1b skipped: hanging a process with SIGSTOP needs Linux or macOS", flush=True)
        names = [n for n in names if n != "F1b"]
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")

    for name in names:
        for run in range(1, args.runs + 1):
            print(f"{name} run {run}/{args.runs} ...", flush=True)
            try:
                result = run_test(TESTS[name], seed=args.seed, run=run, raw_dir=args.out / "raw")
            except KeyboardInterrupt:
                sys.exit(130)  # relays and the temp dir are already cleaned up
            write_runs(args.out / "runs.csv", [result])
            write_timelines(args.out / "timelines.csv", [result])
            print(
                f"  {result.changes} changes, p50 {result.lat_p50_ms:.1f} ms, "
                f"p95 {result.lat_p95_ms:.1f} ms, success {result.success_pct:.1f}%, "
                f"converged {result.converged}, failovers {result.failovers}, "
                f"recovery {result.recovery_s}, loop lag p95 {result.loop_lag_p95_ms:.1f} ms"
                + (" [HARNESS-LIMITED]" if result.harness_limited else ""),
                flush=True,
            )


if __name__ == "__main__":
    main()
