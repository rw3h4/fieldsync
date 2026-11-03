"""Append-only JSON Lines persistence for changes (used by relays and nodes).

One ``Change`` per line, appended when it is delivered (or made locally) and
flushed straight away. The file is never rewritten. Because changes are
appended in delivery order, which is a causal order, reloading is just
offering the saved changes to a fresh ``CausalLog`` in file order.

Limits (documented): ``flush()`` hands the line to the OS, so a killed process
loses nothing, but there is no ``fsync``, so an OS crash or power cut can lose
the last few lines. Those changes are not lost from the system, though: other
replicas still have them and anti-entropy brings them back.
"""

import json
import logging
from pathlib import Path

from fieldsync.crdt import Change

log = logging.getLogger(__name__)


class ChangeStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def load(self) -> list[Change]:
        """All saved changes in saved order.

        A damaged *last* line (the process was killed mid-write) is skipped
        with a warning; a damaged line anywhere else means the file is corrupt
        and raises ``ValueError``.
        """
        lines = self.path.read_text(encoding="utf-8").splitlines()
        changes: list[Change] = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                changes.append(Change.from_json(json.loads(line)))
            except (ValueError, KeyError, TypeError) as exc:
                if number == len(lines):
                    log.warning("%s: skipping damaged last line %d (%s)", self.path, number, exc)
                    continue
                raise ValueError(f"{self.path}: corrupt line {number}: {exc}") from exc
        return changes

    def append(self, change: Change) -> None:
        self._file.write(json.dumps(change.to_json(), separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
