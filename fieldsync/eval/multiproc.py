"""P4-mp: the same load as P4, with the clients spread over several worker processes (D43).

P4 is harness-limited: one Python process hosting 16 replicas saturates its
event loop. Here a coordinator starts the relays (exactly as ``loadgen``), then
``spec.workers`` worker processes, each hosting ``clients / workers`` nodes.

- **One clock:** every process is on this machine and stamps trace events with
  ``time.time()``, so their timestamps are comparable (D26). The coordinator
  hands every worker the same wall-clock start time ``t_go``.
- **Same workload:** client ``i`` uses the same seeded RNG as in ``loadgen``.
  Client c00 (worker 0) creates the 4 records; every worker waits for them.
- **End of run:** after the window, every worker stops editing and waits the
  same fixed ``drain``, then reports its trace, loop-lag samples and each
  node's clock and digest. The coordinator checks convergence across all
  nodes and computes the metrics with the same code as ``loadgen`` (D41).
- **Harness-limited** if *any* worker's loop-lag p95 exceeds 50 ms.
- **Clean-up:** workers are killed and the relays stopped in ``finally`` blocks
  (also on Ctrl+C or a timeout), like ``loadgen``.
"""

import asyncio
import contextlib
import logging
import multiprocessing as mp
import queue
import random
import tempfile
import time
import traceback
from pathlib import Path

from fieldsync.eval.loadgen import (
    HARNESS_LAG_LIMIT_MS,
    RECORDS,
    RelayPair,
    RunResult,
    Spec,
    Trace,
    _client,
    _lag_monitor,
    _wait_until,
    _write_raw,
    base_result,
    window_lag_p95,
)
from fieldsync.net.node import Node

SETUP_MARGIN = 10.0  # seconds between spawning workers and the common start time


def _worker(
    spec: Spec,
    seed: int,
    run: int,
    worker: int,
    clients: list[int],
    urls: list[str],
    workdir: str,
    t_go: float,
    out: "mp.Queue[tuple[str, object]]",
) -> None:
    """Worker process entry point: run its clients, put ("ok", report) or ("error", text)."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    try:
        report = asyncio.run(_worker_main(spec, seed, run, clients, urls, Path(workdir), t_go))
        out.put(("ok", report))
    except BaseException:
        out.put(("error", f"worker {worker}: {traceback.format_exc()}"))


async def _worker_main(
    spec: Spec,
    seed: int,
    run: int,
    clients: list[int],
    urls: list[str],
    workdir: Path,
    t_go: float,
) -> dict[str, object]:
    trace = Trace()
    lag: list[tuple[float, float]] = []
    nodes = [Node(f"c{i:02d}", urls, data_file=workdir / f"c{i:02d}.jsonl") for i in clients]
    tasks: list[asyncio.Task[None]] = []
    try:
        for node in nodes:
            node.replica.observers.append(trace.observer(node.id))
            await node.start()
        if not await _wait_until(lambda: all(n.status.state == "connected" for n in nodes), 15):
            raise RuntimeError("clients did not all connect")
        if 0 in clients:
            creator = nodes[0].replica
            for k in range(RECORDS):
                creator.create_record(f"record {k}", "case_note")
        if not await _wait_until(
            lambda: all(len(n.replica.list_records()) == RECORDS for n in nodes), 15
        ):
            raise RuntimeError("records did not reach every client")
        records = [r.id for r in nodes[0].replica.list_records()]  # same order everywhere

        tasks.append(asyncio.create_task(_lag_monitor(lag)))
        await asyncio.sleep(max(0.0, t_go - time.time()))
        for i, node in zip(clients, nodes, strict=True):
            rng = random.Random(f"{seed}:{run}:{i}")
            tasks.append(asyncio.create_task(_client(node, records, rng, spec.rate)))
        t_end = t_go + spec.warmup + spec.window
        await asyncio.sleep(max(0.0, t_end - time.time()))
        for task in tasks[1:]:
            task.cancel()  # stop editing; keep sampling loop lag
        await asyncio.sleep(max(0.0, t_end + spec.drain - time.time()))
        return {
            "edits": trace.edits,
            "applies": trace.applies,
            "change_receives": trace.change_receives,
            "lag": lag,
            "nodes": [
                (
                    n.id,
                    dict(n.replica.clock.items()),
                    n.replica.state_digest(),
                    len(n.replica.pending),
                    n.status.failovers,
                )
                for n in nodes
            ],
        }
    finally:
        for task in tasks:
            task.cancel()
        for node in nodes:
            with contextlib.suppress(Exception):
                await node.stop()


def run_multi(
    spec: Spec, *, seed: int = 1, run: int = 1, raw_dir: Path | None = None, timeout: float = 300.0
) -> RunResult:
    """Run ``spec`` once with its clients spread over ``spec.workers`` processes."""
    ctx = mp.get_context("spawn")  # the same on Linux, macOS and Windows
    per_worker = spec.clients // spec.workers
    with (
        tempfile.TemporaryDirectory(prefix="fieldsync-loadgen-", ignore_cleanup_errors=True) as tmp,
        RelayPair(Path(tmp)) as relays,
    ):
        urls = [relays.url("a"), relays.url("b")]
        out: mp.Queue[tuple[str, object]] = ctx.Queue()
        t_go = time.time() + SETUP_MARGIN
        procs = [
            ctx.Process(
                target=_worker,
                args=(
                    spec,
                    seed,
                    run,
                    w,
                    list(range(w * per_worker, (w + 1) * per_worker)),
                    urls,
                    tmp,
                    t_go,
                    out,
                ),
                daemon=True,
            )
            for w in range(spec.workers)
        ]
        reports: list[dict[str, object]] = []
        try:
            for p in procs:
                p.start()
            expected_end = t_go + spec.warmup + spec.window + spec.drain + 60
            deadline = min(expected_end, time.time() + timeout)
            while len(reports) < len(procs):
                try:
                    status, payload = out.get(timeout=1.0)
                except queue.Empty:
                    if time.time() > deadline:
                        raise TimeoutError("P4-mp workers did not report in time") from None
                    if sum(p.exitcode is not None for p in procs) > len(reports):
                        raise RuntimeError("a P4-mp worker died without reporting") from None
                    continue
                if status != "ok":
                    raise RuntimeError(str(payload))
                reports.append(payload)  # type: ignore[arg-type]
        finally:
            for p in procs:
                p.join(timeout=10)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=5)

    # Merge the workers' traces and compute the metrics exactly as for one process.
    trace = Trace()
    states = []
    worker_lag_p95 = []
    t0 = t_go + spec.warmup
    for rep in reports:
        trace.edits.extend(rep["edits"])  # type: ignore[arg-type]
        trace.applies.update(rep["applies"])  # type: ignore[arg-type]
        trace.change_receives.extend(rep["change_receives"])  # type: ignore[arg-type]
        states.extend(rep["nodes"])  # type: ignore[arg-type]
        lag = rep["lag"]
        worker_lag_p95.append(window_lag_p95(lag, t0, t0 + spec.window))  # type: ignore[arg-type]
    ids = sorted(s[0] for s in states)
    if raw_dir is not None:
        _write_raw(raw_dir, spec, run, t0, trace)
    worst = max(worker_lag_p95)
    result = base_result(spec, seed, run, trace, ids, t0, worst)
    result.harness_limited = worst > HARNESS_LAG_LIMIT_MS
    result.converged = (
        len({tuple(sorted(s[1].items())) for s in states}) == 1
        and len({s[2] for s in states}) == 1
        and not any(s[3] for s in states)
    )
    result.failovers = sum(s[4] for s in states)
    return result
