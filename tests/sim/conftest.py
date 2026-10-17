from collections.abc import Callable, Sequence

from editor.sim.transport import Envelope


class ManualTransport:
    """Test transport that records outgoing envelopes; tests deliver them by hand."""

    def __init__(self, peers: Sequence[str] = ()) -> None:
        self.sent: list[Envelope] = []
        self._peers = list(peers)
        self.handler: Callable[[Envelope], None] | None = None

    def send(self, envelope: Envelope) -> None:
        self.sent.append(envelope)

    def peers(self) -> Sequence[str]:
        return self._peers

    def set_handler(self, handler: Callable[[Envelope], None]) -> None:
        self.handler = handler

    def take(self) -> list[Envelope]:
        out, self.sent = self.sent, []
        return out
