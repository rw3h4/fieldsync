"""``Simulation``: the single facade used by the UI, scripted scenarios and tests.

It wires replicas to a ``SimNetwork`` through ``SimTransport``s, drives
periodic anti-entropy from the virtual clock, and collects the global trace.
"""

from collections.abc import Iterable, Sequence

from fieldsync.runtime.replica import Replica
from fieldsync.runtime.trace import TraceEvent
from fieldsync.sim.network import InFlight, SimNetwork, SimTransport
from fieldsync.sim.scheduler import Scheduler


class Simulation:
    def __init__(
        self,
        replica_ids: Sequence[str] = ("A", "B", "C"),
        *,
        seed: int = 0,
        latency: float = 0.5,
        jitter: float = 0.2,
        anti_entropy_interval: float | None = 5.0,
    ) -> None:
        self.seed = seed
        self.scheduler = Scheduler(seed)
        self.network = SimNetwork(self.scheduler, latency=latency, jitter=jitter)
        self.replicas: dict[str, Replica] = {}
        self.trace: list[TraceEvent] = []
        self.anti_entropy_interval = anti_entropy_interval
        for rid in replica_ids:
            self.add_replica(rid, join=False)

    # ----------------------------------------------------------- replicas
    def add_replica(self, replica_id: str, *, join: bool = True) -> Replica:
        """Create a replica. With ``join=True`` (late joiner) it requests a full sync at once."""
        if replica_id in self.replicas:
            raise ValueError(f"replica {replica_id!r} already exists")
        replica = Replica(
            replica_id, SimTransport(self.network, replica_id), now=lambda: self.scheduler.now
        )
        replica.observers.append(self.trace.append)
        self.replicas[replica_id] = replica
        # Stagger anti-entropy timers so replicas don't all sync at the same instant.
        self._schedule_anti_entropy(replica_id, first=True)
        if join:
            replica.on_reconnect()
        return replica

    def __getitem__(self, replica_id: str) -> Replica:
        return self.replicas[replica_id]

    # -------------------------------------------------------------- edits
    def edit(self, replica_id: str, index: int, delete: int = 0, insert: str = "") -> None:
        self.replicas[replica_id].edit(index, delete, insert)

    def set_text(self, replica_id: str, text: str) -> None:
        self.replicas[replica_id].set_text(text)

    # ------------------------------------------------------------- faults
    def partition(self, groups: Sequence[Iterable[str]]) -> None:
        self.network.partition(groups)

    def heal(self) -> None:
        """Reconnect all groups. Replicas notice and immediately run anti-entropy."""
        self.network.heal()
        for rid, replica in self.replicas.items():
            if self.network.is_online(rid):
                replica.on_reconnect()

    def set_online(self, replica_id: str, online: bool) -> None:
        was_online = self.network.is_online(replica_id)
        self.network.set_online(replica_id, online)
        if online and not was_online:
            self.replicas[replica_id].on_reconnect()

    def set_hold(self, on: bool) -> None:
        self.network.set_hold(on)

    def deliver(self, msg_id: int) -> None:
        self.network.deliver(msg_id)

    def drop(self, msg_id: int) -> None:
        self.network.drop(msg_id)

    def set_drop_rate(self, rate: float, links: Iterable[tuple[str, str]] | None = None) -> None:
        self.network.set_drop_rate(rate, links)

    def set_reorder(self, on: bool, links: Iterable[tuple[str, str]] | None = None) -> None:
        self.network.set_reorder(on, links)

    # --------------------------------------------------------------- time
    @property
    def now(self) -> float:
        return self.scheduler.now

    def advance(self, dt: float) -> None:
        self.scheduler.run_until(self.scheduler.now + dt)

    def step(self) -> bool:
        """Process exactly one event (message arrival or timer)."""
        return self.scheduler.step()

    def settle(self, max_time: float = 120.0) -> bool:
        """Run until every replica has every change and nothing is in flight (or ``max_time``).

        Periodic anti-entropy keeps running meanwhile, so lost messages are
        recovered. Returns True if the replicas end up fully synchronised.
        """
        deadline = self.scheduler.now + max_time
        while self.scheduler.now <= deadline:
            if not self.scheduler.has_foreground and self.fully_synced():
                return True
            next_time = self.scheduler.next_time
            if next_time is None or next_time > deadline:
                break
            self.scheduler.step()
        return self.fully_synced()

    # ------------------------------------------------------------ queries
    def texts(self) -> dict[str, str]:
        return {rid: r.text() for rid, r in self.replicas.items()}

    def converged(self) -> bool:
        """Same visible text on every replica (what the convergence badges show)."""
        return len({r.doc.digest() for r in self.replicas.values()}) <= 1

    def fully_synced(self) -> bool:
        """Every replica has applied the same set of changes and buffers nothing."""
        clocks = {r.clock for r in self.replicas.values()}
        return len(clocks) <= 1 and not any(r.pending for r in self.replicas.values())

    def messages(self) -> list[InFlight]:
        return self.network.messages

    # ---------------------------------------------------------- internals
    def _schedule_anti_entropy(self, replica_id: str, *, first: bool = False) -> None:
        interval = self.anti_entropy_interval
        if interval is None:
            return
        delay = interval * (self.scheduler.rng.uniform(0.5, 1.0) if first else 1.0)

        def tick() -> None:
            # A replica knows when it is disconnected, so it doesn't sync while offline.
            if self.anti_entropy_interval is not None and self.network.is_online(replica_id):
                self.replicas[replica_id].anti_entropy()
            self._schedule_anti_entropy(replica_id)

        self.scheduler.schedule(delay, tick, background=True)
