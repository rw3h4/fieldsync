"""Cursor anchoring: keep a text cursor next to the same characters when remote edits arrive.

A cursor stored as an integer index drifts when a remote edit inserts or
deletes text before it. Instead we anchor each end of the selection to the
CRDT element just before it (``RGA.id_before``). Element IDs never change, so
after a remote change we map the anchors back to indices with
``RGA.index_after``. If an anchor's character was deleted, the cursor lands
right after the nearest visible character before it.

Pure functions, no UI: unit-tested in tests/ui/test_cursor.py.
"""

from fieldsync.crdt import RGA, ElementId

Anchors = tuple[ElementId, ElementId]


def anchors(doc: RGA, start: int, end: int) -> Anchors:
    """Anchor a selection ``[start, end)`` (indices into the current visible text)."""
    return doc.id_before(start), doc.id_before(end)


def restore(doc: RGA, anchored: Anchors) -> tuple[int, int]:
    """Indices of an anchored selection in the document as it is now."""
    start, end = (doc.index_after(a) for a in anchored)
    return start, max(start, end)
