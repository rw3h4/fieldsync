"""Simulated unreliable network: latency, jitter, loss, reordering, partitions, hold mode.

Every message copy (one per destination) gets an ``InFlight`` record, so the UI
and the space-time diagram can show the full history of the network, including
dropped messages.

Fault model:
- **Latency/jitter**: delivery takes ``latency ± jitter`` virtual seconds.
- **FIFO links** by default: a message never overtakes an earlier one on the
  same directed link. With ``reorder`` on, a link adds an extra random delay
  and drops the FIFO guarantee.
- **Loss**: with probability ``drop_rate`` per link, a message is lost. It is
  decided at send time from the seeded RNG and shown at its would-be arrival.
- **Partitions/offline**: checked at *delivery* time. A message whose sender
  and receiver are not connected when it would arrive is dropped.
- **Hold mode**: messages are parked as ``held`` and wait for the presenter to
  ``deliver`` or ``drop`` them, in any order.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from fieldsync.runtime.transport import BROADCAST, Envelope
from fieldsync.sim.scheduler import Scheduler

Status = Literal["in_flight", "held", "delivered", "dropped"]


@dataclass
class LinkConfig:
    drop_rate: float = 0.0
    reorder: bool = False


@dataclass
class InFlight:
    id: int
    envelope: Envelope
    src: str
    dst: str
    sent_at: float
    status: Status = "in_flight"
    deliver_at: float | None = None  # scheduled arrival (None while held)
    ended_at: float | None = None  # when it was delivered or dropped
    drop_reason: str | None = None
    lost: bool = field(default=False, repr=False)  # loss decided at send time


class SimNetwork:
    def __init__(self, scheduler: Scheduler, latency: float = 0.5, jitter: float = 0.2) -> None:
        self.scheduler = scheduler
        self.latency = latency
        self.jitter = jitter
        self.hold = False
        self.messages: list[InFlight] = []
        self._handlers: dict[str, Callable[[Envelope], None]] = {}
        self._links: dict[tuple[str, str], LinkConfig] = {}
        self._group: dict[str, int] = {}  # partition group per replica
        self._offline: set[str] = set()
        self._last_arrival: dict[tuple[str, str], float] = {}  # for FIFO links
        self.observers: list[Callable[[InFlight], None]] = []  # notified on every status change

    # ------------------------------------------------------------ membership
    def register(self, replica_id: str, handler: Callable[[Envelope], None]) -> None:
        self._handlers[replica_id] = handler
        self._group.setdefault(replica_id, 0)

    @property
    def replica_ids(self) -> list[str]:
        return list(self._handlers)

    def peers_of(self, replica_id: str) -> list[str]:
        return [r for r in self._handlers if r != replica_id]

    # ---------------------------------------------------------- fault knobs
    def link(self, src: str, dst: str) -> LinkConfig:
        return self._links.setdefault((src, dst), LinkConfig())

    def set_drop_rate(self, rate: float, links: Iterable[tuple[str, str]] | None = None) -> None:
        """Set the loss probability on the given directed links (default: all links)."""
        for src, dst in links if links is not None else self._all_links():
            self.link(src, dst).drop_rate = rate

    def set_reorder(self, on: bool, links: Iterable[tuple[str, str]] | None = None) -> None:
        for src, dst in links if links is not None else self._all_links():
            self.link(src, dst).reorder = on

    def partition(self, groups: Sequence[Iterable[str]]) -> None:
        """Split replicas into groups that can only talk within themselves."""
        assigned = {r: i for i, group in enumerate(groups) for r in group}
        # Replicas not mentioned get a group of their own.
        for i, r in enumerate(self._handlers):
            self._group[r] = assigned.get(r, len(groups) + i)

    def heal(self) -> None:
        for r in self._group:
            self._group[r] = 0

    def set_online(self, replica_id: str, online: bool) -> None:
        (self._offline.discard if online else self._offline.add)(replica_id)

    def is_online(self, replica_id: str) -> bool:
        return replica_id not in self._offline

    def connected(self, a: str, b: str) -> bool:
        return (
            a not in self._offline
            and b not in self._offline
            and self._group.get(a) == self._group.get(b)
        )

    def groups(self) -> list[list[str]]:
        by_group: dict[int, list[str]] = {}
        for r in self._handlers:
            by_group.setdefault(self._group[r], []).append(r)
        return list(by_group.values())

    def set_hold(self, on: bool) -> None:
        """Toggle hold mode. Turning it off releases held messages with normal latency."""
        self.hold = on
        if not on:
            for msg in self.held():
                self._schedule(msg)

    # ------------------------------------------------------------- sending
    def send(self, src: str, env: Envelope) -> None:
        dsts = self.peers_of(src) if env.dst == BROADCAST else [env.dst]
        for dst in dsts:
            if dst not in self._handlers:
                continue
            msg = InFlight(len(self.messages), env, src, dst, self.scheduler.now)
            msg.lost = self.scheduler.rng.random() < self.link(src, dst).drop_rate
            self.messages.append(msg)
            if self.hold:
                msg.status = "held"
                self._notify(msg)
            else:
                self._schedule(msg)

    def _schedule(self, msg: InFlight) -> None:
        rng = self.scheduler.rng
        delay = max(0.01, self.latency + rng.uniform(-self.jitter, self.jitter))
        key = (msg.src, msg.dst)
        if self.link(*key).reorder:
            delay += rng.uniform(0, 2 * self.latency)
            arrival = self.scheduler.now + delay
        else:
            arrival = max(self.scheduler.now + delay, self._last_arrival.get(key, 0.0) + 1e-6)
            self._last_arrival[key] = arrival
        msg.status = "in_flight"
        msg.deliver_at = arrival
        self.scheduler.schedule_at(arrival, lambda: self._arrive(msg))
        self._notify(msg)

    # ------------------------------------------------------------ delivery
    def _arrive(self, msg: InFlight) -> None:
        if msg.status != "in_flight":
            return  # already handled manually
        if msg.lost:
            self._drop(msg, "lost")
        elif not self.connected(msg.src, msg.dst):
            self._drop(
                msg,
                "partitioned" if self.is_online(msg.dst) and self.is_online(msg.src) else "offline",
            )
        else:
            self._deliver(msg)

    def deliver(self, msg_id: int) -> None:
        """Hold mode: the presenter delivers this message now (partitions still apply)."""
        msg = self.messages[msg_id]
        if msg.status not in ("held", "in_flight"):
            return
        if not self.connected(msg.src, msg.dst):
            self._drop(msg, "partitioned")
        else:
            self._deliver(msg)

    def drop(self, msg_id: int) -> None:
        msg = self.messages[msg_id]
        if msg.status in ("held", "in_flight"):
            self._drop(msg, "dropped by presenter")

    def _deliver(self, msg: InFlight) -> None:
        msg.status = "delivered"
        msg.ended_at = self.scheduler.now
        self._notify(msg)
        self._handlers[msg.dst](msg.envelope)

    def _drop(self, msg: InFlight, reason: str) -> None:
        msg.status = "dropped"
        msg.ended_at = self.scheduler.now
        msg.drop_reason = reason
        self._notify(msg)

    # ------------------------------------------------------------- queries
    def held(self) -> list[InFlight]:
        return [m for m in self.messages if m.status == "held"]

    def in_flight(self) -> list[InFlight]:
        return [m for m in self.messages if m.status in ("in_flight", "held")]

    def _all_links(self) -> list[tuple[str, str]]:
        ids = list(self._handlers)
        return [(a, b) for a in ids for b in ids if a != b]

    def _notify(self, msg: InFlight) -> None:
        for observer in self.observers:
            observer(msg)


class SimTransport:
    """``Transport`` implementation backed by ``SimNetwork``."""

    def __init__(self, network: SimNetwork, replica_id: str) -> None:
        self._network = network
        self._id = replica_id
        self._handler: Callable[[Envelope], None] | None = None
        network.register(replica_id, self._dispatch)

    def send(self, envelope: Envelope) -> None:
        self._network.send(self._id, envelope)

    def peers(self) -> Sequence[str]:
        return self._network.peers_of(self._id)

    def set_handler(self, handler: Callable[[Envelope], None]) -> None:
        self._handler = handler

    def _dispatch(self, envelope: Envelope) -> None:
        if self._handler is not None:
            self._handler(envelope)
