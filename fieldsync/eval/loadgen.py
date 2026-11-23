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
   Warm-up, then the measurement window, during which F1/F2 inject their fault.
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
import socket
import string
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from fieldsync.eval.metrics import Edit, backlog_cleared, pair_latencies, percentile
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
    fault: str | None = None  # "kill_relay" or "offline_half"
    fault_at: float = 0.0  # seconds into the window
    fault_len: float = 0.0  # offline duration (F2)


TESTS: dict[str, Spec] = {
    "P1": Spec("P1", clients=2, rate=1.0),
    "P2": Spec("P2", clients=4, rate=5.0),
    "P3": Spec("P3", clients=8, rate=5.0),
    "P4": Spec("P4", clients=16, rate=10.0),
    "F1": Spec("F1", clients=4, rate=5.0, fault="kill_relay", fault_at=15.0),
    "F2": Spec(
        "F2", clients=4, rate=5.0, window=50.0, fault="offline_half", fault_at=10.0, fault_len=30.0
    ),
}
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
    recovery_s: float | None
    loop_lag_p95_ms: float
    harness_limited: bool


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

    def close(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
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
    fault_t: float | None = None
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
        if spec.fault == "kill_relay":
            fault_t = time.time()
            relays.kill("a")
            b = relays.url("b")
            if await _wait_until(
                lambda: all(n.status.state == "connected" and n.status.url == b for n in nodes),
                spec.window - spec.fault_at,
            ):
                failover_s = time.time() - fault_t
        elif spec.fault == "offline_half":
            half = nodes[: spec.clients // 2]
            for node in half:
                node.set_offline(True)
            await asyncio.sleep(spec.fault_len)
            fault_t = time.time()  # F2 measures from the reconnect
            for node in half:
                node.set_offline(False)
        await asyncio.sleep(max(0.0, t_end - time.time()))

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

    window_edits = [e for e in trace.edits if t0 <= e.t < t_end]
    latencies, expected = pair_latencies(window_edits, trace.applies, ids)
    recovery = (
        backlog_cleared(trace.edits, trace.applies, ids, fault_t) if fault_t is not None else None
    )
    lag_ms = [s * 1000 for t, s in lag if t0 <= t < t_end]
    lag_p95 = percentile(lag_ms, 95)
    if raw_dir is not None:
        _write_raw(raw_dir, spec, run, t0, trace)
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
        converged=converged,
        failovers=failovers,
        fault_at_s=fault_t - t0 if fault_t is not None else None,
        failover_s=failover_s,
        recovery_s=recovery,
        loop_lag_p95_ms=lag_p95,
        harness_limited=lag_p95 > HARNESS_LAG_LIMIT_MS,
    )


def run_test(
    spec: Spec, *, seed: int = 1, run: int = 1, raw_dir: Path | None = None, timeout: float = 300.0
) -> RunResult:
    """Run one test once, from a fresh temporary directory and fresh relays."""
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


def write_runs(path: Path, results: Sequence[RunResult]) -> None:
    """Upsert rows into runs.csv, keyed by (test, run): re-running replaces old rows."""
    columns = [f.name for f in fields(RunResult)]
    rows: dict[tuple[str, str], dict[str, str]] = {}
    if path.exists():
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                rows[(row["test"], row["run"])] = row
    for r in results:
        rows[(r.test, str(r.run))] = {k: _fmt(v) for k, v in asdict(r).items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for key in sorted(rows):
            w.writerow(rows[key])


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
    names = sorted(TESTS) if args.all else (args.test or parser.error("give --test or --all"))
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")

    for name in names:
        for run in range(1, args.runs + 1):
            print(f"{name} run {run}/{args.runs} ...", flush=True)
            try:
                result = run_test(TESTS[name], seed=args.seed, run=run, raw_dir=args.out / "raw")
            except KeyboardInterrupt:
                sys.exit(130)  # relays and the temp dir are already cleaned up
            write_runs(args.out / "runs.csv", [result])
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
