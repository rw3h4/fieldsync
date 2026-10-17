"""Logical clocks: Lamport clocks (for element IDs) and vector clocks (for causality)."""

from collections.abc import Iterable, Iterator, Mapping


class LamportClock:
    """Scalar logical clock.

    Rules (Lamport, 1978):
    - before a local event, ``tick()`` increments the counter;
    - on receiving a remote timestamp ``n``, ``observe(n)`` sets the counter to
      ``max(local, n)`` so the next local tick is greater than anything seen.

    This gives: if event a happened-before b then L(a) < L(b). The RGA relies on
    it: a character's ID is always larger than the ID of the character it was
    inserted after.
    """

    __slots__ = ("value",)

    def __init__(self, value: int = 0) -> None:
        self.value = value

    def tick(self) -> int:
        self.value += 1
        return self.value

    def observe(self, remote: int) -> None:
        self.value = max(self.value, remote)


class VectorClock:
    """Immutable vector clock: replica ID -> number of changes seen from it.

    Missing entries mean 0. Zero entries are never stored, so two clocks that
    describe the same knowledge compare and hash equal.

    Partial order:
    - ``a <= b`` iff ``a[r] <= b[r]`` for every replica r
      (everything a knows about, b knows about too);
    - ``a.happened_before(b)`` iff ``a <= b`` and ``a != b``;
    - ``a.concurrent_with(b)`` iff neither happened before the other.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: Mapping[str, int] | None = None) -> None:
        self._entries: dict[str, int] = {r: n for r, n in (entries or {}).items() if n > 0}

    # --- reading -------------------------------------------------------------
    def get(self, replica: str) -> int:
        return self._entries.get(replica, 0)

    def replicas(self) -> Iterator[str]:
        return iter(sorted(self._entries))

    def items(self) -> Iterable[tuple[str, int]]:
        return sorted(self._entries.items())

    # --- deriving new clocks -------------------------------------------------
    def increment(self, replica: str) -> "VectorClock":
        entries = dict(self._entries)
        entries[replica] = entries.get(replica, 0) + 1
        return VectorClock(entries)

    def merge(self, other: "VectorClock") -> "VectorClock":
        """Pointwise maximum: the knowledge of both clocks combined."""
        entries = dict(self._entries)
        for replica, n in other._entries.items():
            entries[replica] = max(entries.get(replica, 0), n)
        return VectorClock(entries)

    # --- partial order -------------------------------------------------------
    def __le__(self, other: "VectorClock") -> bool:
        return all(n <= other.get(r) for r, n in self._entries.items())

    def __lt__(self, other: "VectorClock") -> bool:
        return self <= other and self != other

    def __ge__(self, other: "VectorClock") -> bool:
        return other <= self

    def __gt__(self, other: "VectorClock") -> bool:
        return other < self

    def happened_before(self, other: "VectorClock") -> bool:
        return self < other

    def concurrent_with(self, other: "VectorClock") -> bool:
        return not (self <= other) and not (other <= self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VectorClock) and self._entries == other._entries

    def __hash__(self) -> int:
        return hash(frozenset(self._entries.items()))

    # --- serialisation / display --------------------------------------------
    def to_dict(self) -> dict[str, int]:
        return dict(self.items())

    @classmethod
    def from_dict(cls, data: Mapping[str, int]) -> "VectorClock":
        return cls({str(r): int(n) for r, n in data.items()})

    def __repr__(self) -> str:
        return f"VectorClock({self.to_dict()!r})"

    def __str__(self) -> str:
        return "[" + " ".join(f"{r}:{n}" for r, n in self.items()) + "]"
