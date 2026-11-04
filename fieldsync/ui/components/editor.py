"""``RecordEditor``: a textarea bound to one record of a ``Replica`` (shared by node page and sim).

Data flow:
- **Local typing:** the textarea's new value goes to ``Replica.set_text``,
  which diffs it against the record's text and makes one ``Change``.
- **Remote changes:** the replica emits an ``apply`` trace event for the
  record; the editor reloads the text and puts the cursor back where it was,
  using CRDT anchors (see ``cursor.py``) rather than integer indices.

The UI only displays state and forwards user input; it never resolves
conflicts. Must be used from the event loop that owns the replica.

Remote changes arrive on a background task (the transport's reader), where
NiceGUI has no "current page". So the editor talks to its own browser tab
explicitly (``self.textarea.client``) and never through ``ui.*`` calls that
need a page context. It also never lets a UI error escape into the replica:
the observer runs inside the replica's causal delivery.

The browser's text can lag behind the replica's (remote edits are in flight
to the tab). A cursor position only means something together with the text
it was measured in, so both directions carry the text:
- selection reports from the browser (on keyup and click, i.e. user actions
  only) include its text, and are ignored when that text is not the
  replica's current text (the anchors are kept);
- a restore tells the browser which text it was computed for; the browser
  applies only the newest restore, once the textarea shows that text.
"""

import json
import logging

from nicegui import ui

from fieldsync.runtime import Replica, TraceEvent
from fieldsync.ui.components.cursor import Anchors, anchors, restore

# Browser helper: set a textarea's selection once it shows the text the selection
# was computed for. Only the newest request per textarea counts; older ones are dropped.
_JS_HELPER = """
<script>
window.fieldsyncLatest = {};
window.fieldsyncSetSelection = (id, start, end, text) => {
  const token = {};
  window.fieldsyncLatest[id] = token;
  let tries = 0;
  const apply = () => {
    if (window.fieldsyncLatest[id] !== token) return;  // superseded by a newer restore
    const host = document.getElementById('c' + id);
    const area = host && (host.tagName === 'TEXTAREA' ? host : host.querySelector('textarea'));
    if (!area || document.activeElement !== area) return;
    if (area.value !== text) {  // Vue hasn't rendered this text yet: try again shortly
      if (tries++ < 50) setTimeout(apply, 20);
      return;
    }
    area.setSelectionRange(start, end);
  };
  setTimeout(apply, 0);
};
</script>
"""
_SELECTION_JS = "(e) => emit(e.target.selectionStart, e.target.selectionEnd, e.target.value)"

log = logging.getLogger(__name__)


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
        # User actions only. Not "select": our own setSelectionRange fires it, and it can
        # arrive after the next remote value has moved the browser's caret to the end.
        for event in ("keyup", "click"):
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
            # Via this tab's client, not ui.run_javascript: we may be on a background task.
            # The helper waits until the tab shows `text` before setting the selection.
            self.textarea.client.run_javascript(
                f"fieldsyncSetSelection({self.textarea.id}, {start}, {end}, {json.dumps(text)})"
            )

    # ------------------------------------------------------------ handlers
    def _on_input(self, event: object) -> None:
        if self._loading or self._record is None:
            return
        self._replica.set_text(self._record, self.textarea.value or "")

    def _on_selection(self, event: object) -> None:
        if self._record is None:
            return
        try:
            raw_start, raw_end, shown = event.args  # type: ignore[attr-defined]
            start, end = int(raw_start), int(raw_end)
        except (TypeError, ValueError):
            return
        if shown != self._replica.text(self._record):
            # Measured in older text than the replica's (a remote edit is still on its
            # way to the tab, or this event came from our own restore of an older text).
            # Keep the current anchors; the pending refresh restores from them.
            return
        doc = self._replica.document(self._record)
        n = len(doc)
        self._anchors = anchors(doc, min(start, n), min(end, n))

    def _on_trace(self, event: TraceEvent) -> None:
        if event.kind == "apply" and event.record == self._record:
            try:
                self.refresh()
            except Exception:  # a display problem must never interrupt causal delivery
                log.exception("editor refresh failed for %s", self._record)

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
