"""Append-only JSON Lines persistence for changes (used by relays and nodes).

One ``Change`` per line, appended when it is delivered (or made locally).
Because changes are appended in delivery order, which is a causal order,
reloading is just offering the saved changes to a fresh ``CausalLog`` in file
order.

Each line goes straight to the OS (unbuffered), so a killed process loses
nothing. Saved lines are never rewritten; the only truncation is this one: a
write that fails part-way (e.g. disk full) is cut back to where its
line started before the error is re-raised, so a broken line never stays in
the middle of the file and the caller's retry appends cleanly (D29).

Limits (documented): there is no ``fsync``, so an OS crash or power cut can lose
the last few lines. Those changes are not lost from the system, though: other
replicas still have them and anti-entropy brings them back.
"""

import json
import logging
import os
from pathlib import Path

from fieldsync.crdt import Change

log = logging.getLogger(__name__)


class ChangeStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Binary, unbuffered append: we write exact bytes (LF on every OS, so data
        # files are byte-identical across platforms) and no half-written line can
        # sit in a Python buffer after a failed write.
        self._file = self.path.open("ab", buffering=0)

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
        line = (json.dumps(change.to_json(), separators=(",", ":")) + "\n").encode("utf-8")
        start = os.fstat(self._file.fileno()).st_size  # where this line begins
        try:
            view = memoryview(line)
            while view:  # a raw write may be partial
                written = self._file.write(view)
                view = view[written:]
        except OSError:
            # Remove whatever part of the line reached the file. If the truncate
            # itself fails, its error propagates instead, still an OSError, so
            # the caller keeps reporting the failure.
            self._file.truncate(start)
            raise

    def close(self) -> None:
        self._file.close()
