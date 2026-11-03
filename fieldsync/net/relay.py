"""Relay server: forwards changes between nodes, keeps a durable log, syncs with its peer relay.

A relay is deliberately "dumb": it **never orders or merges** edits and holds
no documents. Convergence comes only from the CRDT inside each node. A relay
is a ``CausalLog`` (the same class a node uses) behind a WebSocket server:

- **ChangeMsg / SyncResponse:** every change goes through ``log.offer``. If
  this is the first time the relay sees it ("delivered" or "buffered"), it is
  forwarded once as a ChangeMsg to every other connected node and to each
  peer relay, never back to where it came from. Duplicates are not
  forwarded, so two relays can never bounce a change back and forth.
- **SyncRequest:** answered by the relay itself (``answer_sync``: the missing
  changes, plus a counter-request if the requester knows more than we do).
  A sync request is **never forwarded**: one node's sync would otherwise
  turn into a storm of syncs across the system.
- **Heartbeat ping:** answered with a pong (same ``sent_at``).
- **Hello:** says whether the other side is a node or a peer relay.

Durability: every delivered change is appended to a JSON Lines file; on
start the relay reloads it by offering the saved changes in order, so a
restarted relay can serve late joiners the full history.

Relay-to-relay: for each ``--peer`` URL the relay connects as a client with
``Hello("relay")`` (heartbeats and backoff included) and runs anti-entropy
with the peer on every (re)connect and every ``anti_entropy_interval``.
"""

import argparse
import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from itertools import count
from pathlib import Path

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from fieldsync.crdt import Change, check_replica_id
from fieldsync.net.client import ReconnectingClient
from fieldsync.net.store import ChangeStore
from fieldsync.runtime import (
    BROADCAST,
    CausalLog,
    ChangeMsg,
    Envelope,
    Heartbeat,
    Hello,
    SyncRequest,
    SyncResponse,
    answer_sync,
    decode,
    encode,
)
from fieldsync.runtime.transport import Payload

log = logging.getLogger(__name__)

Send = Callable[[Envelope], None]


class _Outbox:
    """Ordered sending on one server-side connection (a queue drained by one task)."""

    def __init__(self, conn: ServerConnection) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task = asyncio.create_task(self._drain(conn))

    def send(self, env: Envelope) -> None:
        self._queue.put_nowait(encode(env))

    async def _drain(self, conn: ServerConnection) -> None:
        with contextlib.suppress(ConnectionClosed):
            while True:
                await conn.send(await self._queue.get())

    def close(self) -> None:
        self._task.cancel()


class Relay:
    def __init__(
        self,
        relay_id: str,
        *,
        log_file: Path | str | None = None,
        peer_urls: Sequence[str] = (),
        anti_entropy_interval: float | None = 2.0,
        heartbeat_interval: float = 1.0,
        heartbeat_timeout: float = 3.0,
    ) -> None:
        self.id = check_replica_id(relay_id)
        self._store = ChangeStore(log_file) if log_file is not None else None
        self.log = CausalLog(relay_id, on_deliver=self._persist)
        self.anti_entropy_interval = anti_entropy_interval
        self.stats: Counter[str] = Counter()  # message counters (used by the no-loop test)
        self._peer_urls = list(peer_urls)
        self._hb = (heartbeat_interval, heartbeat_timeout)
        self._nodes: dict[str, _Outbox] = {}  # node id -> its connection
        self._peers_in: dict[str, _Outbox] = {}  # peer relays connected to us
        self._peers_out: list[ReconnectingClient] = []  # our links to peer relays
        self._msg_ids = count(1)
        self._server: Server | None = None
        self._ae_task: asyncio.Task[None] | None = None
        self._running = asyncio.Event()  # cleared while paused (simulates a hung relay)
        self._running.set()
        self._loading = False

    # ================================================================ life
    async def start(self, host: str = "0.0.0.0", port: int = 8765) -> int:
        """Reload the log, start serving and connect to peers. Returns the actual port."""
        if self._store is not None:
            self._loading = True  # don't re-append what we are reading back
            for change in self._store.load():
                self.log.offer(change)
            self._loading = False
            log.info("%s: reloaded %d changes", self.id, len(self.log.history))
        self._server = await serve(self._serve, host, port, ping_interval=None)
        actual = self._server.sockets[0].getsockname()[1]
        for url in self._peer_urls:
            self.connect_peer(url)
        if self.anti_entropy_interval is not None:
            self._ae_task = asyncio.create_task(self._anti_entropy_loop())
        log.info("%s: listening on %s:%d", self.id, host, actual)
        return actual

    def connect_peer(self, url: str) -> None:
        """Link to a peer relay (can be called after start, e.g. once its port is known)."""
        link = ReconnectingClient(
            self.id,
            "relay",
            [url],
            on_message=lambda env: self._handle(env, self._link_key(link), link.send),
            heartbeat_interval=self._hb[0],
            heartbeat_timeout=self._hb[1],
        )
        link.on_connected.append(lambda: self._sync_with(link.send))
        self._peers_out.append(link)
        link.start()

    async def stop(self) -> None:
        """Stop serving and drop every connection (what a crash looks like to clients)."""
        if self._ae_task is not None:
            self._ae_task.cancel()
        for link in self._peers_out:
            await link.stop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for outbox in [*self._nodes.values(), *self._peers_in.values()]:
            outbox.close()
        self._nodes.clear()
        self._peers_in.clear()
        if self._store is not None:
            self._store.close()

    def pause(self) -> None:
        """Stop processing messages but keep sockets open: a hung relay (for failover tests)."""
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    @property
    def node_ids(self) -> list[str]:
        return sorted(self._nodes)

    # ============================================================ serving
    async def _serve(self, conn: ServerConnection) -> None:
        outbox = _Outbox(conn)
        key: str | None = None
        registry: dict[str, _Outbox] | None = None
        try:
            async for text in conn:
                await self._running.wait()
                try:
                    env = decode(str(text))
                except (ValueError, KeyError, TypeError) as exc:
                    log.warning("%s: bad message ignored (%s)", self.id, exc)
                    continue
                if isinstance(env.payload, Hello):
                    key = env.src
                    registry = self._nodes if env.payload.role == "node" else self._peers_in
                    registry[key] = outbox  # a reconnect replaces the old connection
                    self.stats[f"hello_{env.payload.role}"] += 1
                    continue
                self._handle(env, key, outbox.send)
        except ConnectionClosed:
            pass
        finally:
            if key is not None and registry is not None and registry.get(key) is outbox:
                del registry[key]
            outbox.close()

    def _handle(self, env: Envelope, source: str | None, reply: Send) -> None:
        """React to one message from ``source`` (a node or relay id); ``reply`` answers it."""
        if not self._running.is_set():
            return  # paused ("hung"): nothing gets answered, not even heartbeats
        p = env.payload
        self.stats[f"recv_{env.kind}"] += 1
        if isinstance(p, Heartbeat):
            if not p.pong:
                reply(self._envelope(Heartbeat(p.sent_at, pong=True), env.src))
        elif isinstance(p, SyncRequest):
            # Answered here, never forwarded.
            for payload in answer_sync(self.log, p):
                reply(self._envelope(payload, env.src))
        elif isinstance(p, ChangeMsg):
            self._take(p.change, source)
        elif isinstance(p, SyncResponse):
            for change in p.changes:
                self._take(change, source)

    def _take(self, change: Change, source: str | None) -> None:
        outcome = self.log.offer(change)
        if outcome == "duplicate":
            self.stats["duplicates"] += 1
            return
        # First sight: pass it on once. Duplicates stop here, so relays can't loop.
        self._forward(change, source)

    def _forward(self, change: Change, source: str | None) -> None:
        env = self._envelope(ChangeMsg(change), BROADCAST)
        for node_id, outbox in self._nodes.items():
            if node_id != source:
                outbox.send(env)
                self.stats["fwd_node"] += 1
        for peer_id, send in self._peer_senders().items():
            if peer_id != source:
                send(env)
                self.stats["fwd_peer"] += 1

    # ======================================================= relay peering
    def _peer_senders(self) -> dict[str, Send]:
        """One way to reach each peer relay, even if we're linked in both directions."""
        senders: dict[str, Send] = {}
        for link in self._peers_out:
            if link.connected:
                senders.setdefault(self._link_key(link), link.send)
        for peer_id, outbox in self._peers_in.items():
            senders.setdefault(peer_id, outbox.send)
        return senders

    def _link_key(self, link: ReconnectingClient) -> str:
        return link.status.relay_id or link.urls[0]  # relay id once its first pong arrived

    def _sync_with(self, send: Send) -> None:
        send(self._envelope(SyncRequest(self.log.clock), BROADCAST))
        self.stats["sync_peer"] += 1

    async def _anti_entropy_loop(self) -> None:
        assert self.anti_entropy_interval is not None
        while True:
            await asyncio.sleep(self.anti_entropy_interval)
            await self._running.wait()
            for link in self._peers_out:
                if link.connected:
                    self._sync_with(link.send)

    # ============================================================ helpers
    def _persist(self, change: Change) -> None:
        if self._store is not None and not self._loading:
            self._store.append(change)

    def _envelope(self, payload: Payload, dst: str) -> Envelope:
        return Envelope(f"{self.id}:{next(self._msg_ids)}", self.id, dst, payload)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="relay", description="FieldSync relay server")
    parser.add_argument("--id", required=True, help="relay id, e.g. relay_a (letters, digits, _)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--peer", action="append", default=[], help="ws:// URL of a peer relay")
    parser.add_argument("--log-file", type=Path, help="JSON Lines file to persist the op log")
    parser.add_argument("--anti-entropy", type=float, default=2.0, help="peer sync interval (s)")
    parser.add_argument("--heartbeat", type=float, default=1.0, help="peer ping interval (s)")
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    async def run() -> None:
        relay = Relay(
            args.id,
            log_file=args.log_file,
            peer_urls=args.peer,
            anti_entropy_interval=args.anti_entropy,
            heartbeat_interval=args.heartbeat,
            heartbeat_timeout=args.heartbeat_timeout,
        )
        await relay.start(args.host, args.port)
        try:
            await asyncio.Future()  # run until Ctrl+C
        finally:
            await relay.stop()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
