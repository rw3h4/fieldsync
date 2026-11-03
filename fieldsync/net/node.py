"""``Node``: one editor node, headless (replica + WsTransport + persistence + anti-entropy).

No UI imports: tests and the M4 load generator run nodes without a browser,
and the NiceGUI page (``fieldsync.ui.node``) sits on top of this class.

Everything runs on one asyncio event loop. ``Replica`` is synchronous and is
only ever called from that loop: by the transport's reader task, by the
anti-entropy timer, and by UI event handlers.

- **On every (re)connect** (including after a failover) the node runs
  ``replica.on_reconnect()``: a sync request that makes the relay send what we
  lack and pull what it lacks. This is how edits made while offline, or lost
  in flight during a failover, reach the system.
- **Anti-entropy:** ``replica.anti_entropy()`` every ``anti_entropy_interval``
  seconds while connected.
- **Persistence:** every change that enters the replica's history (local
  creates and edits, and remote changes) is appended to a JSON Lines file. On
  start the file is replayed through ``replica.log.offer``, so a restarted
  node keeps everything, including edits it made while offline.
- **Time:** trace events carry wall-clock seconds (``now``, default
  ``time.time``), so events from different processes on one machine can be
  compared (M4 measures sync latency this way). The UI shows them relative to
  the node's start.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from fieldsync.net.client import Backoff, ClientStatus
from fieldsync.net.store import ChangeStore
from fieldsync.net.ws_transport import WsTransport
from fieldsync.runtime import Replica, TraceEvent


class Node:
    def __init__(
        self,
        node_id: str,
        relay_urls: Sequence[str],
        *,
        data_file: Path | str | None = None,
        anti_entropy_interval: float | None = 2.0,
        heartbeat_interval: float = 1.0,
        heartbeat_timeout: float = 3.0,
        backoff: Backoff | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.transport = WsTransport(
            node_id,
            relay_urls,
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout,
            backoff=backoff,
        )
        self.replica = Replica(node_id, self.transport, now=now)
        self.started_at = now()
        self.anti_entropy_interval = anti_entropy_interval
        self._store = ChangeStore(data_file) if data_file is not None else None
        self._saved = 0  # how many history entries are already in the file
        self._ae_task: asyncio.Task[None] | None = None
        self.transport.on_connected.append(self.replica.on_reconnect)

    @property
    def id(self) -> str:
        return self.replica.id

    @property
    def status(self) -> ClientStatus:
        return self.transport.status

    # ================================================================ life
    async def start(self) -> None:
        if self._store is not None:
            for change in self._store.load():
                self.replica.log.offer(change)  # replay in saved (causal) order
            self._saved = len(self.replica.history)
            self.replica.observers.append(self._persist_new)
        self.transport.start()
        if self.anti_entropy_interval is not None:
            self._ae_task = asyncio.create_task(self._anti_entropy_loop())

    async def stop(self) -> None:
        if self._ae_task is not None:
            self._ae_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ae_task
        await self.transport.stop()
        if self._store is not None:
            self._store.close()

    def set_offline(self, offline: bool) -> None:
        """Manual offline switch (FR4 demo): disconnect and stop reconnecting."""
        self.transport.set_offline(offline)

    # ============================================================ internals
    def _persist_new(self, _event: TraceEvent) -> None:
        # History only grows, in causal order; write whatever is new.
        assert self._store is not None
        history = self.replica.history
        while self._saved < len(history):
            self._store.append(history[self._saved])
            self._saved += 1

    async def _anti_entropy_loop(self) -> None:
        assert self.anti_entropy_interval is not None
        while True:
            await asyncio.sleep(self.anti_entropy_interval)
            if self.transport.client.connected:
                self.replica.anti_entropy()
