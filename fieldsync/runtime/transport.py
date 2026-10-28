"""Messages exchanged between replicas, and the ``Transport`` abstraction.

The replica runtime (``fieldsync.runtime.replica``) only talks to a ``Transport``.
The simulator (``SimTransport``) and real network mode (``WsTransport``)
each implement it, so the causal buffer and anti-entropy logic are identical
in both modes.

Wire format (JSON)::

    {"type": "change",    "msg_id": "A:7", "src": "A", "dst": "*", "body": {<Change>}}
    {"type": "sync_req",  "msg_id": "B:3", "src": "B", "dst": "A",
     "body": {"clock": {"A": 2}, "reciprocate": true}}
    {"type": "sync_resp", "msg_id": "A:8", "src": "A", "dst": "B",
     "body": {"changes": [<Change>, ...], "clock": {...}}}
    {"type": "hello",     "msg_id": "C:1", "src": "C", "dst": "*", "body": {"role": "node"}}
    {"type": "heartbeat", "msg_id": "C:2", "src": "C", "dst": "A",
     "body": {"sent_at": 12.5, "pong": false}}
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fieldsync.crdt import Change, VectorClock

BROADCAST = "*"


@dataclass(frozen=True, slots=True)
class ChangeMsg:
    """A freshly made local change, pushed to peers."""

    change: Change


@dataclass(frozen=True, slots=True)
class SyncRequest:
    """Anti-entropy: "this is what I have (``clock``), send me what I'm missing".

    ``reciprocate=True`` also asks the receiver to send its own SyncRequest
    back if it is missing something from us (push-pull). Counter-requests set
    it to False, so the exchange always ends.
    """

    clock: VectorClock
    reciprocate: bool = True


@dataclass(frozen=True, slots=True)
class SyncResponse:
    """Anti-entropy reply: the changes the requester was missing, in causal order."""

    changes: tuple[Change, ...]
    clock: VectorClock


Role = Literal["node", "relay"]


@dataclass(frozen=True, slots=True)
class Hello:
    """Network mode only: introduces the sender to a relay.

    ``role`` lets a relay tell editor nodes (which it forwards changes to)
    from other relays (which it keeps in sync with relay-to-relay anti-entropy).
    """

    role: Role


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Network mode only: liveness check between a node and its relay.

    A ping has ``pong=False``; the receiver answers with ``pong=True`` and the
    same ``sent_at``, so the pinger can measure the round-trip time. Missing
    pongs for longer than the timeout mean the relay is presumed dead and the
    node fails over. Handled by the transport layer; the replica ignores it.
    """

    sent_at: float
    pong: bool = False


Payload = ChangeMsg | SyncRequest | SyncResponse | Hello | Heartbeat


@dataclass(frozen=True, slots=True)
class Envelope:
    """A payload addressed from ``src`` to ``dst`` (a replica ID or ``BROADCAST``)."""

    msg_id: str
    src: str
    dst: str
    payload: Payload

    @property
    def kind(self) -> str:
        return _KIND[type(self.payload)]

    def summary(self) -> str:
        p = self.payload
        if isinstance(p, ChangeMsg):
            return p.change.summary()
        if isinstance(p, SyncRequest):
            return f"sync_req {p.clock}" + ("" if p.reciprocate else " (counter)")
        if isinstance(p, SyncResponse):
            return f"sync_resp {len(p.changes)} change(s)"
        if isinstance(p, Heartbeat):
            return "pong" if p.pong else "ping"
        return f"hello ({p.role})"


class Transport(Protocol):
    """What the replica runtime needs from a network.

    Delivery is unreliable and unordered: messages may be delayed, reordered,
    duplicated or lost. The runtime copes with all of these through causal
    buffering, idempotence and anti-entropy.
    """

    def send(self, envelope: Envelope) -> None:
        """Fire-and-forget. ``envelope.dst`` may be ``BROADCAST``."""
        ...

    def peers(self) -> Sequence[str]:
        """Known peer IDs (may be empty when unknown, e.g. behind a relay)."""
        ...

    def set_handler(self, handler: Callable[[Envelope], None]) -> None:
        """Register the callback that receives incoming envelopes."""
        ...


# ---------------------------------------------------------------- JSON codec
_KIND: dict[type, str] = {
    ChangeMsg: "change",
    SyncRequest: "sync_req",
    SyncResponse: "sync_resp",
    Hello: "hello",
    Heartbeat: "heartbeat",
}


def _payload_to_json(p: Payload) -> dict[str, Any]:
    if isinstance(p, ChangeMsg):
        return p.change.to_json()
    if isinstance(p, SyncRequest):
        return {"clock": p.clock.to_dict(), "reciprocate": p.reciprocate}
    if isinstance(p, SyncResponse):
        return {"changes": [c.to_json() for c in p.changes], "clock": p.clock.to_dict()}
    if isinstance(p, Heartbeat):
        return {"sent_at": p.sent_at, "pong": p.pong}
    return {"role": p.role}


def _payload_from_json(kind: str, body: dict[str, Any]) -> Payload:
    if kind == "change":
        return ChangeMsg(Change.from_json(body))
    if kind == "sync_req":
        return SyncRequest(VectorClock.from_dict(body["clock"]), bool(body["reciprocate"]))
    if kind == "sync_resp":
        return SyncResponse(
            tuple(Change.from_json(c) for c in body["changes"]),
            VectorClock.from_dict(body["clock"]),
        )
    if kind == "hello":
        role = body["role"]
        if role not in ("node", "relay"):
            raise ValueError(f"unknown role {role!r}")
        return Hello(role)
    if kind == "heartbeat":
        return Heartbeat(float(body["sent_at"]), bool(body["pong"]))
    raise ValueError(f"unknown message type {kind!r}")


def envelope_to_json(env: Envelope) -> dict[str, Any]:
    return {
        "type": env.kind,
        "msg_id": env.msg_id,
        "src": env.src,
        "dst": env.dst,
        "body": _payload_to_json(env.payload),
    }


def envelope_from_json(data: dict[str, Any]) -> Envelope:
    return Envelope(
        msg_id=str(data["msg_id"]),
        src=str(data["src"]),
        dst=str(data["dst"]),
        payload=_payload_from_json(str(data["type"]), data["body"]),
    )


def encode(env: Envelope) -> str:
    return json.dumps(envelope_to_json(env), separators=(",", ":"))


def decode(text: str) -> Envelope:
    return envelope_from_json(json.loads(text))
