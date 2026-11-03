"""Helpers for in-process network tests: real WebSockets on 127.0.0.1, one event loop."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

from fieldsync.net.client import Backoff
from fieldsync.net.node import Node
from fieldsync.net.relay import Relay

# Short timings so failures are detected in well under a second.
FAST = {"heartbeat_interval": 0.1, "heartbeat_timeout": 0.3}


def run(scenario: Callable[[], Awaitable[None]], timeout: float = 20.0) -> None:
    """Run an async test scenario on a fresh event loop, with an overall timeout."""
    asyncio.run(asyncio.wait_for(scenario(), timeout))


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what or predicate}")
        await asyncio.sleep(0.02)


async def start_relay(
    relay_id: str, *, log_file: Path | None = None, **kw: object
) -> tuple[Relay, str]:
    relay = Relay(relay_id, log_file=log_file, **{**FAST, **kw})  # type: ignore[arg-type]
    port = await relay.start("127.0.0.1", 0)
    return relay, f"ws://127.0.0.1:{port}"


def make_node(node_id: str, urls: list[str], **kw: object) -> Node:
    options: dict[str, object] = {
        **FAST,
        "anti_entropy_interval": None,
        "backoff": Backoff(initial=0.05, cap=0.3),
        **kw,
    }
    return Node(node_id, urls, **options)  # type: ignore[arg-type]


async def start_node(node_id: str, urls: list[str], **kw: object) -> Node:
    node = make_node(node_id, urls, **kw)
    await node.start()
    await wait_until(lambda: node.status.state == "connected", what=f"{node_id} connected")
    return node


def converged(nodes: Iterable[Node]) -> bool:
    nodes = list(nodes)
    return (
        len({n.replica.state_digest() for n in nodes}) == 1
        and all(not n.replica.pending for n in nodes)
        and len({n.replica.clock for n in nodes}) == 1
    )


async def stop_all(*things: Node | Relay) -> None:
    for thing in things:
        await thing.stop()
