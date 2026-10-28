"""Trace events emitted by replicas: the raw material for logs and diagrams."""

from dataclasses import dataclass
from typing import Literal

from fieldsync.crdt import VectorClock

EventKind = Literal[
    "edit",  # local edit turned into a Change and applied
    "send",  # envelope handed to the transport
    "receive",  # envelope arrived from the transport
    "buffer",  # change held back: causal dependencies missing
    "apply",  # remote change integrated into the CRDT
    "duplicate",  # change already seen; ignored (idempotence)
]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    time: float  # virtual time (sim) or seconds since start (network mode)
    replica: str
    kind: EventKind
    clock: VectorClock  # the replica's vector clock *after* the event
    lamport: int  # the replica's Lamport clock after the event
    detail: str  # human-readable summary
    msg_id: str | None = None  # envelope involved (send/receive), if any
    peer: str | None = None  # the other side: dst for send, src for receive
    dot: tuple[str, int] | None = None  # (origin, seq) of the change involved, if any
