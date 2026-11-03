"""``RecordEditor``: a textarea bound to one record of a ``Replica`` (shared by node page and sim).

Data flow:
- **Local typing:** the textarea's new value goes to ``Replica.set_text``,
  which diffs it against the record's text and makes one ``Change``.
- **Remote changes:** the replica emits an ``apply`` trace event for the
  record; the editor reloads the text and puts the cursor back where it was,
  using CRDT anchors (see ``cursor.py``) rather than integer indices.

The UI only displays state and forwards user input; it never resolves
conflicts. Must be used from the event loop that owns the replica.
"""

from nicegui import ui

from fieldsync.runtime import Replica, TraceEvent
from fieldsync.ui.components.cursor import Anchors, anchors, restore

# Small browser helper: set a textarea's selection after Vue has re-rendered its value.
_JS_HELPER = """
<script>
window.fieldsyncSetSelection = (id, start, end) => {
  setTimeout(() => {
    const host = document.getElementById('c' + id);
    const area = host && (host.tagName === 'TEXTAREA' ? host : host.querySelector('textarea'));
    if (area && document.activeElement === area) area.setSelectionRange(start, end);
  }, 0);
};
</script>
"""
_SELECTION_JS = "(e) => emit(e.target.selectionStart, e.target.selectionEnd)"


class RecordEditor(ui.column):
    def __init__(self, replica: Replica, record_id: str | None = None, *, label: str = "") -> None:
        super().__init__()
        self.classes("w-full")
        self._replica = replica
        self._record: str | None = None
        self._anchors: Anchors | None = None
        self._loading = False  # True while we set the value ourselves (not user input)
        ui.add_head_html(_JS_HELPER, shared=True)
        with self:
            self.textarea = (
                ui.textarea(label=label, on_change=self._on_input)
                .props("outlined autogrow")
                .classes("w-full font-mono")
            )
        for event in ("keyup", "click", "select"):
            self.textarea.on(event, self._on_selection, js_handler=_SELECTION_JS)
        replica.observers.append(self._on_trace)
        self.set_record(record_id)

    # ----------------------------------------------------------------- API
    @property
    def record(self) -> str | None:
        return self._record

    def set_record(self, record_id: str | None) -> None:
        """Show another record (or none)."""
        self._record = record_id
        self._anchors = None
        self.textarea.set_enabled(record_id is not None)
        self._load(self._replica.text(record_id) if record_id is not None else "")

    def refresh(self) -> None:
        """Reload the record's text after a remote change, keeping the cursor in place."""
        if self._record is None:
            return
        text = self._replica.text(self._record)
        if text == self.textarea.value:
            return
        self._load(text)
        if self._anchors is not None:
            start, end = restore(self._replica.document(self._record), self._anchors)
            ui.run_javascript(f"fieldsyncSetSelection({self.textarea.id}, {start}, {end})")

    # ------------------------------------------------------------ handlers
    def _on_input(self, event: object) -> None:
        if self._loading or self._record is None:
            return
        self._replica.set_text(self._record, self.textarea.value or "")

    def _on_selection(self, event: object) -> None:
        if self._record is None:
            return
        try:
            start, end = (int(v) for v in event.args)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            return
        doc = self._replica.document(self._record)
        n = len(doc)
        self._anchors = anchors(doc, min(start, n), min(end, n))

    def _on_trace(self, event: TraceEvent) -> None:
        if event.kind == "apply" and event.record == self._record:
            self.refresh()

    def _load(self, text: str) -> None:
        self._loading = True
        try:
            self.textarea.set_value(text)
        finally:
            self._loading = False

    def _handle_delete(self) -> None:
        # Stop listening to the replica when the page (or this editor) goes away.
        if self._on_trace in self._replica.observers:
            self._replica.observers.remove(self._on_trace)
        super()._handle_delete()
