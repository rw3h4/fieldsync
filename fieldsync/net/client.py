"""``ReconnectingClient``: a WebSocket client with heartbeats, failover and backoff.

Used by editor nodes (through ``WsTransport``) to talk to a relay, and by a
relay to talk to its peer relay.

Failure detection
-----------------
After connecting, the client says ``Hello(role)`` and then sends a
``Heartbeat`` ping every ``heartbeat_interval`` seconds. The relay echoes each
ping as a pong carrying the same ``sent_at``, which also gives the round-trip
time. If no pong arrives for ``heartbeat_timeout`` seconds, the relay is
presumed dead (crashed, hung, or the network is down) and the connection is
dropped. A closed or broken connection counts as dead immediately.
websockets' own keepalive is switched off so this heartbeat is the only
failure detector, and its behaviour is the one we document and test.

Failover
--------
The client holds an ordered URL list ``[A, B, ...]``. When the current relay
dies it tries the next one at once. Only after a whole round of failures does
it wait, with exponential backoff and a cap. It stays on whichever relay
works: after failing over from A to B it does **not** switch back when A
returns (no flapping); if B later dies it tries A again.

While disconnected, ``send`` drops messages on purpose. For a node, the
replica's op log is the real outbox: on every (re)connect the node runs a
sync, and the relay pulls whatever it is missing.
"""

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import count
from typing import Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from fieldsync.runtime import BROADCAST, Envelope, Heartbeat, Hello, decode, encode
from fieldsync.runtime.transport import Role

log = logging.getLogger(__name__)

State = Literal["connecting", "connected", "offline", "manual_offline"]


@dataclass(frozen=True)
class ClientStatus:
    """What the status bar shows (FR5)."""

    state: State = "connecting"
    url: str | None = None  # relay in use (or being tried)
    relay_id: str | None = None  # learned from the relay's pongs
    rtt_ms: float | None = None  # last heartbeat round-trip time
    failovers: int = 0  # times a working relay died and we moved on
    since: float = 0.0  # wall-clock time of the last state change


class Backoff:
    """Exponential backoff with a cap and a little jitter: 0.5, 1, 2, 4, ... seconds."""

    def __init__(
        self,
        initial: float = 0.5,
        cap: float = 10.0,
        jitter: float = 0.1,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.initial, self.cap, self.jitter, self._rng = initial, cap, jitter, rng
        self._next = initial

    def next(self) -> float:
        delay = self._next
        self._next = min(self.cap, self._next * 2)
        return delay * (1 + self.jitter * (2 * self._rng() - 1))

    def reset(self) -> None:
        self._next = self.initial


class ReconnectingClient:
    def __init__(
        self,
        own_id: str,
        role: Role,
        urls: Sequence[str],
        *,
        on_message: Callable[[Envelope], None],
        heartbeat_interval: float = 1.0,
        heartbeat_timeout: float = 3.0,
        backoff: Backoff | None = None,
    ) -> None:
        if not urls:
            raise ValueError("need at least one relay URL")
        self.own_id = own_id
        self.role = role
        self.urls = list(urls)
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.backoff = backoff or Backoff()
        self.status = ClientStatus(since=time.time())
        self.on_status: list[Callable[[ClientStatus], None]] = []
        self.on_connected: list[Callable[[], None]] = []  # called after Hello on each (re)connect
        self._on_message = on_message
        self._msg_ids = count(1)
        self._queue: asyncio.Queue[str] | None = None  # outbox of the live connection
        self._kick = asyncio.Event()  # ends the current session (manual offline / stop)
        self._wake = asyncio.Event()  # interrupts a backoff wait or an offline pause
        self._manual_offline = False
        self._stopped = False
        self._task: asyncio.Task[None] | None = None

    # ================================================================== API
    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        self._kick.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def send(self, env: Envelope) -> bool:
        """Queue ``env`` on the live connection. Returns False (dropped) while disconnected."""
        if self._queue is None:
            return False
        self._queue.put_nowait(encode(env))
        return True

    def set_offline(self, offline: bool) -> None:
        """Manual offline switch: drop the connection and stop reconnecting until switched back."""
        self._manual_offline = offline
        if offline:
            self._kick.set()
        self._wake.set()

    @property
    def connected(self) -> bool:
        return self.status.state == "connected"

    # ============================================================ main loop
    async def _run(self) -> None:
        index = 0  # current relay; we stay on it while it works (no flapping back)
        failed_in_a_row = 0
        while not self._stopped:
            if self._manual_offline:
                self._set_status(state="manual_offline")
                await self._wait_for_wake(None)
                continue
            url = self.urls[index]
            self._set_status(state="connecting", url=url)
            was_alive = await self._session(url)
            if self._stopped:
                break
            if self._manual_offline:
                continue  # switched off by hand: not a failure, stay on this relay
            if was_alive:
                # A relay that answered heartbeats has died: fail over to the next one now.
                self.backoff.reset()
                failed_in_a_row = 0
                self._set_status(failovers=self.status.failovers + 1)
                log.warning("%s: lost %s, failing over", self.own_id, url)
            else:
                failed_in_a_row += 1
            index = (index + 1) % len(self.urls)
            if failed_in_a_row >= len(self.urls):
                # Every relay failed in a row: wait before the next round.
                failed_in_a_row = 0
                self._set_status(state="offline", url=None)
                await self._wait_for_wake(self.backoff.next())

    async def _session(self, url: str) -> bool:
        """Connect and run until the connection dies. True if the relay ever answered a ping."""
        try:
            conn = await connect(
                url, open_timeout=self.heartbeat_timeout, ping_interval=None, close_timeout=0.5
            )
        except (OSError, TimeoutError, ValueError) as exc:
            log.info("%s: cannot connect to %s (%s)", self.own_id, url, exc)
            return False
        except Exception as exc:  # handshake rejected etc.
            log.info("%s: cannot connect to %s (%r)", self.own_id, url, exc)
            return False

        self._kick.clear()
        self._alive = False
        self._last_pong = time.monotonic()
        queue: asyncio.Queue[str] = asyncio.Queue()
        await conn.send(encode(self._envelope(Hello(self.role))))
        self._queue = queue
        self._set_status(state="connected", url=url, relay_id=None, rtt_ms=None)
        for callback in list(self.on_connected):
            callback()
        tasks = [
            asyncio.create_task(self._reader(conn)),
            asyncio.create_task(self._writer(conn, queue)),
            asyncio.create_task(self._heartbeat(queue)),
            asyncio.create_task(self._kick.wait()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._queue = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Abort rather than wait for a closing handshake a dead relay won't answer.
            conn.transport.abort()
        return self._alive

    # =============================================================== tasks
    async def _reader(self, conn: ClientConnection) -> None:
        with contextlib.suppress(ConnectionClosed):
            async for text in conn:
                try:
                    env = decode(str(text))
                except (ValueError, KeyError, TypeError) as exc:
                    log.warning("%s: bad message ignored (%s)", self.own_id, exc)
                    continue
                if isinstance(env.payload, Heartbeat):
                    self._on_heartbeat(env)
                    continue
                try:
                    self._on_message(env)
                except Exception:
                    log.exception("%s: error handling %s", self.own_id, env.summary())

    async def _writer(self, conn: ClientConnection, queue: asyncio.Queue[str]) -> None:
        with contextlib.suppress(ConnectionClosed):
            while True:
                await conn.send(await queue.get())

    async def _heartbeat(self, queue: asyncio.Queue[str]) -> None:
        """Ping regularly; return (= relay presumed dead) when pongs stop for the timeout."""
        while True:
            queue.put_nowait(encode(self._envelope(Heartbeat(time.monotonic()))))
            await asyncio.sleep(self.heartbeat_interval)
            silent_for = time.monotonic() - self._last_pong
            if silent_for > self.heartbeat_timeout:
                log.warning("%s: no pong for %.1fs, relay presumed dead", self.own_id, silent_for)
                return

    def _on_heartbeat(self, env: Envelope) -> None:
        beat = env.payload
        assert isinstance(beat, Heartbeat)
        if beat.pong:
            now = time.monotonic()
            self._last_pong = now
            self._alive = True
            self._set_status(relay_id=env.src, rtt_ms=round((now - beat.sent_at) * 1000, 1))
        elif self._queue is not None:  # someone pinged us: answer
            self._queue.put_nowait(
                encode(self._envelope(Heartbeat(beat.sent_at, pong=True), dst=env.src))
            )

    # ============================================================= helpers
    def _envelope(self, payload: Hello | Heartbeat, dst: str = BROADCAST) -> Envelope:
        return Envelope(f"{self.own_id}:hb{next(self._msg_ids)}", self.own_id, dst, payload)

    async def _wait_for_wake(self, timeout: float | None) -> None:
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout)

    def _set_status(self, **changes: object) -> None:
        new = replace(self.status, **changes)  # type: ignore[arg-type]
        if new.state != self.status.state:
            new = replace(new, since=time.time())
        if new == self.status:
            return
        self.status = new
        for callback in list(self.on_status):
            callback(new)
