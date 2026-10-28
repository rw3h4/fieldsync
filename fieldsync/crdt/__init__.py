"""Pure CRDT core: element IDs, logical clocks, ops and the RGA sequence.

This package has no I/O, no asyncio and no UI imports, so it can be tested and
reasoned about in isolation.
"""

from fieldsync.crdt.clocks import LamportClock, VectorClock
from fieldsync.crdt.diff import TextEdit, diff
from fieldsync.crdt.ids import HEAD, ElementId
from fieldsync.crdt.ops import (
    RECORD_KINDS,
    Change,
    CreateRecord,
    Delete,
    Insert,
    Op,
    RecordKind,
    check_replica_id,
    op_from_json,
    op_to_json,
    record_id_for,
)
from fieldsync.crdt.rga import RGA, Element, MissingDependency

__all__ = [
    "HEAD",
    "RECORD_KINDS",
    "RGA",
    "Change",
    "CreateRecord",
    "Delete",
    "Element",
    "ElementId",
    "Insert",
    "LamportClock",
    "MissingDependency",
    "Op",
    "RecordKind",
    "TextEdit",
    "VectorClock",
    "check_replica_id",
    "diff",
    "op_from_json",
    "op_to_json",
    "record_id_for",
]
