"""Pure CRDT core: element IDs, logical clocks, ops and the RGA sequence.

This package has no I/O, no asyncio and no UI imports, so it can be tested and
reasoned about in isolation.
"""

from editor.crdt.clocks import LamportClock, VectorClock
from editor.crdt.diff import TextEdit, diff
from editor.crdt.ids import HEAD, ElementId
from editor.crdt.ops import Change, Delete, Insert, Op, op_from_json, op_to_json
from editor.crdt.rga import RGA, Element, MissingDependency

__all__ = [
    "HEAD",
    "RGA",
    "Change",
    "Delete",
    "Element",
    "ElementId",
    "Insert",
    "LamportClock",
    "MissingDependency",
    "Op",
    "TextEdit",
    "VectorClock",
    "diff",
    "op_from_json",
    "op_to_json",
]
