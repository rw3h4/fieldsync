"""The editor node page (FR1-FR5). Displays state and forwards user actions; decides nothing.

Layout: status bar (FR5) on top; record list with "New record" (FR1, FR2) on
the left; the open record's editor (FR3) in the middle; the event log at the
bottom. The Offline switch (FR4 demo) disconnects this node's transport.
"""

import logging

from nicegui import ui

from fieldsync.crdt import RecordKind
from fieldsync.net.client import ClientStatus
from fieldsync.net.node import Node
from fieldsync.runtime import TraceEvent
from fieldsync.ui.components.editor import RecordEditor

KIND_LABELS: dict[RecordKind, str] = {"visit_report": "Visit report", "case_note": "Case note"}
LOG_LINES = 200

log = logging.getLogger(__name__)


def connection_text(status: ClientStatus) -> str:
    """One line for the status bar: which relay we use, or why we're offline."""
    if status.state == "connected":
        relay = status.relay_id or "relay"
        rtt = f" · RTT {status.rtt_ms:.0f} ms" if status.rtt_ms is not None else ""
        return f"● {relay} · {status.url}{rtt}"
    if status.state == "connecting":
        return f"◌ connecting to {status.url}"
    if status.state == "manual_offline":
        return "○ OFFLINE (manual)"
    return "○ OFFLINE (all relays down, retrying)"


def format_event(event: TraceEvent, started_at: float) -> str:
    return f"{event.time - started_at:8.2f}s  {event.kind:<9} {event.detail}  {event.clock}"


class NodePage:
    def __init__(self, node: Node) -> None:
        self.node = node
        self.replica = node.replica
        self._known_records = -1

        with ui.header().classes("items-center gap-4 bg-slate-800"):
            ui.label(f"FieldSync · {node.id}").classes("text-lg font-bold")
            self.connection = ui.label().classes("font-mono")
            self.persist_warning = ui.label().classes(
                "font-bold text-white bg-red-600 px-2 rounded"
            )
            ui.space()
            self.offline = ui.switch("Offline", on_change=lambda e: node.set_offline(bool(e.value)))
        with ui.row().classes("w-full gap-6 font-mono text-sm text-slate-600"):
            self.clock = ui.label()
            self.pending = ui.label()
            self.digests = ui.label()
            self.failovers = ui.label()

        with ui.row().classes("w-full no-wrap items-start gap-4"):
            with ui.column().classes("w-72 shrink-0"):
                ui.button("New record", icon="add", on_click=self._new_record_dialog)
                self.record_list = ui.column().classes("w-full gap-1")
            with ui.column().classes("grow"):
                self.record_title = ui.label("No record open").classes("text-lg font-semibold")
                self.editor = RecordEditor(self.replica, None, label="Record text")

        ui.label("Event log").classes("mt-4 font-semibold")
        self.log = ui.log(max_lines=LOG_LINES).classes("w-full h-48 text-xs")

        self.replica.observers.append(self._on_trace)
        ui.context.client.on_delete(self._detach)
        ui.timer(0.25, self._refresh_status)
        self._refresh_status()
        self._refresh_records()

    # ------------------------------------------------------------- records
    def _refresh_records(self) -> None:
        records = self.replica.list_records()
        self._known_records = len(records)
        self.record_list.clear()
        with self.record_list:
            if not records:
                ui.label("No records yet").classes("text-slate-500")
            for info in records:
                selected = info.id == self.editor.record
                with (
                    ui.card()
                    .classes("w-full cursor-pointer p-2" + (" bg-blue-50" if selected else ""))
                    .on("click", lambda _, rid=info.id: self._open(rid))
                ):
                    ui.label(info.title).classes("font-medium")
                    ui.label(f"{KIND_LABELS[info.kind]} · {info.id}").classes(
                        "text-xs text-slate-500"
                    )

    def _open(self, record_id: str) -> None:
        info = self.replica.record(record_id)
        self.editor.set_record(record_id)
        self.record_title.set_text(f"{info.title}  ({KIND_LABELS[info.kind]} · {info.id})")
        self._refresh_records()

    def _new_record_dialog(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("New record").classes("text-lg font-semibold")
            title = ui.input("Title").classes("w-full")
            kind = ui.select(KIND_LABELS, label="Type", value="visit_report").classes("w-full")

            def create() -> None:
                if not (title.value or "").strip():
                    ui.notify("Give the record a title", type="warning")
                    return
                record_id = self.replica.create_record(title.value.strip(), kind.value)
                dialog.close()
                self._open(record_id)

            with ui.row():
                ui.button("Create", on_click=create)
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    # -------------------------------------------------------------- status
    def _refresh_status(self) -> None:
        status = self.node.status
        self.connection.set_text(connection_text(status))
        self.offline.set_value(status.state == "manual_offline")
        error = status.persist_error
        self.persist_warning.set_visibility(error is not None)
        if error is not None:
            since = error.since - self.node.started_at  # same time base as the event log
            self.persist_warning.set_text(
                f"DATA FILE WRITE FAILING since {since:.1f}s: {error.message}"
            )
        self.clock.set_text(f"clock {self.replica.clock}")
        self.pending.set_text(f"pending {len(self.replica.pending)}")
        record = self.editor.record
        doc = f"record {self.replica.digest(record)}" if record is not None else "record -"
        self.digests.set_text(f"{doc} · state {self.replica.state_digest()}")
        self.failovers.set_text(f"failovers {status.failovers}")

    def _on_trace(self, event: TraceEvent) -> None:
        # Runs inside the replica's causal delivery (often on the transport's task):
        # a display error must not escape into it.
        try:
            self.log.push(format_event(event, self.node.started_at))
            if len(self.replica.list_records()) != self._known_records:
                self._refresh_records()
        except Exception:
            log.exception("node page update failed")

    def _detach(self) -> None:
        if self._on_trace in self.replica.observers:
            self.replica.observers.remove(self._on_trace)


def register(node: Node) -> None:
    @ui.page("/")
    def index() -> None:
        NodePage(node)
