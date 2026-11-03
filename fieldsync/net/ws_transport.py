"""``WsTransport``: the frozen ``Transport`` protocol over WebSockets, through a relay.

The replica runtime doesn't know it is on a real network: it calls
``send``/``peers``/``set_handler`` exactly as with ``SimTransport``. Failover,
heartbeats and backoff live in ``ReconnectingClient``. Heartbeats are handled
there and never reach the replica.
"""

from collections.abc import Callable, Sequence

from fieldsync.net.client import Backoff, ClientStatus, ReconnectingClient
from fieldsync.runtime import Envelope


class WsTransport:
    def __init__(
        self,
        node_id: str,
        relay_urls: Sequence[str],
        *,
        heartbeat_interval: float = 1.0,
        heartbeat_timeout: float = 3.0,
        backoff: Backoff | None = None,
    ) -> None:
        self._handler: Callable[[Envelope], None] | None = None
        self.client = ReconnectingClient(
            node_id,
            "node",
            relay_urls,
            on_message=self._dispatch,
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout,
            backoff=backoff,
        )

    # ------------------------------------------------- Transport protocol
    def send(self, envelope: Envelope) -> None:
        self.client.send(envelope)  # dropped while offline; the op log is the outbox

    def peers(self) -> Sequence[str]:
        return []  # behind a relay: anti-entropy broadcasts and the relay answers

    def set_handler(self, handler: Callable[[Envelope], None]) -> None:
        self._handler = handler

    # ------------------------------------------------------ network mode
    @property
    def status(self) -> ClientStatus:
        return self.client.status

    @property
    def on_connected(self) -> list[Callable[[], None]]:
        return self.client.on_connected

    def set_offline(self, offline: bool) -> None:
        self.client.set_offline(offline)

    def start(self) -> None:
        self.client.start()

    async def stop(self) -> None:
        await self.client.stop()

    def _dispatch(self, envelope: Envelope) -> None:
        if self._handler is not None:
            self._handler(envelope)
