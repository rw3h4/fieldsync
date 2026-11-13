"""Simulator UI page (M3): replica panes, network fault controls, space-time SVG, CRDT internals.

Built strictly against the frozen interfaces in ``docs/INTERFACES.md``.
The UI displays state and forwards user actions to ``Simulation``; it never
implements conflict-resolution logic itself.
"""

from html import escape

from nicegui import ui

from fieldsync.crdt import HEAD, Element, RecordKind
from fieldsync.runtime import RecordInfo, Replica, TraceEvent
from fieldsync.sim import InFlight, Simulation
from fieldsync.sim.scenarios import SCENARIOS, SCENARIOS_BY_ID
from fieldsync.ui.components.editor import RecordEditor
from fieldsync.ui.sim.spacetime import render_spacetime_svg

KIND_LABELS: dict[RecordKind, str] = {
    "visit_report": "Visit report",
    "case_note": "Case note",
}
JOINER_POOL: tuple[str, ...] = ("A", "B", "C", "D")
MAX_LOG_ITEMS = 120


# --------------------------------------------------------- pure format helpers
def convergence_status(sim: Simulation) -> tuple[str, str]:
    """Return ``(badge_text, tailwind_classes)`` for the global simulation state."""
    if sim.converged() and sim.fully_synced():
        return "● CONVERGED & SYNCED", "bg-emerald-600 text-white"
    if sim.converged():
        return "◐ CONVERGED (syncing clocks)", "bg-sky-600 text-white"
    return "▲ DIVERGED", "bg-amber-500 text-slate-950"


def format_groups(sim: Simulation) -> str:
    """Human-readable partition topology string, e.g. ``[A] | [B, C]``."""
    groups = sim.network.groups()
    if len(groups) <= 1:
        return "Connected: [" + ", ".join(sim.replicas) + "]"
    return "Partitioned: " + " | ".join("[" + ", ".join(g) + "]" for g in groups)


def format_pending(replica: Replica) -> list[str]:
    """Format buffered changes and their missing causal dependencies for ``replica``."""
    lines: list[str] = []
    for change in replica.pending:
        needs = ", ".join(f"{r}#{n}" for r, n in replica.waiting_on(change).items())
        lines.append(f"{change.summary()} (waits for {needs})")
    return lines


def format_inflight(msg: InFlight) -> str:
    """One-line summary of a network message for the queue and message log."""
    extra = f" [{msg.drop_reason}]" if msg.drop_reason else ""
    t_end = f" -> {msg.ended_at:.2f}s" if msg.ended_at is not None else ""
    return (
        f"#{msg.id}  {msg.sent_at:.2f}s{t_end}  {msg.src}->{msg.dst}  "
        f"[{msg.status}{extra}]  {msg.envelope.summary()}"
    )


def format_trace_event(event: TraceEvent) -> str:
    """One-line summary of a replica trace event."""
    return (
        f"t={event.time:6.2f}s  [{event.replica}]  {event.kind:<9}  "
        f"{event.detail}  clock={event.clock}"
    )


def format_char(ch: str) -> str:
    """Visible glyph for whitespace characters in the CRDT internals strip."""
    if ch == " ":
        return "␣"
    if ch == "\n":
        return "↵"
    if ch == "\t":
        return "⇥"
    return ch


def render_elements_html(elements: tuple[Element, ...] | list[Element]) -> str:
    """Render an RGA element list (including tombstones) as compact HTML chips."""
    if not elements:
        return '<div class="text-xs text-slate-400 italic py-1">Empty document (HEAD only)</div>'
    chips: list[str] = []
    for el in elements:
        glyph = escape(format_char(el.char))
        eid = escape(str(el.id))
        after = "HEAD" if el.after == HEAD else escape(str(el.after))
        if el.deleted:
            box_cls = "border-red-300 bg-red-50 text-red-700"
            char_cls = "line-through font-bold text-sm"
            badge = '<span class="text-[9px] uppercase font-bold text-red-600">tombstone</span>'
        else:
            box_cls = "border-slate-300 bg-white text-slate-900"
            char_cls = "font-bold text-sm"
            badge = '<span class="text-[9px] text-emerald-700">visible</span>'
        chips.append(
            f'<div class="inline-flex flex-col items-center border rounded px-2 py-1 '
            f'font-mono {box_cls}">'
            f'<span class="{char_cls}">{glyph}</span>'
            f'<span class="text-[10px] text-slate-600">id {eid}</span>'
            f'<span class="text-[9px] text-slate-400">after {after}</span>'
            f"{badge}</div>"
        )
    return '<div class="flex flex-wrap gap-1.5 py-1">' + "".join(chips) + "</div>"


def all_known_records(sim: Simulation) -> list[RecordInfo]:
    """Union of records known to any replica in ``sim``, sorted by ``(origin, seq)``."""
    by_id: dict[str, RecordInfo] = {}
    for replica in sim.replicas.values():
        for info in replica.list_records():
            by_id.setdefault(info.id, info)
    return sorted(by_id.values(), key=lambda r: (r.origin, r.seq))


# --------------------------------------------------------------- simulator UI
class SimPage:
    def __init__(self, *, seed: int = 0) -> None:
        self.seed = seed
        self.playing = False
        self.include_sync = True
        self.selected_scenario = SCENARIOS[0].id
        self.active_record: str | None = None
        self.editors: dict[str, RecordEditor] = {}
        self.sim = SCENARIOS[0].setup(seed)
        records = all_known_records(self.sim)
        self.active_record = records[0].id if records else None

        self._build_header()
        self._build_toolbar()
        self.replica_row = ui.row().classes("w-full items-stretch gap-3 no-wrap overflow-x-auto")
        self._build_network_and_queue_panel()
        self._build_spacetime_panel()
        self._build_internals_and_logs_panel()

        self._rebuild_replica_cards()
        self._refresh_all()
        ui.timer(0.2, self._tick)

    # --------------------------------------------------------------- builders
    def _build_header(self) -> None:
        with ui.header().classes("items-center gap-3 bg-slate-900 text-white px-4 py-2"):
            ui.label("FieldSync · Simulator").classes("text-lg font-bold tracking-tight")
            self.clock_badge = ui.label("t = 0.00s").classes(
                "font-mono text-sm bg-slate-800 px-2.5 py-1 rounded"
            )
            self.conv_badge = ui.label().classes("font-mono text-xs font-bold px-2.5 py-1 rounded")
            self.inflight_badge = ui.label().classes("font-mono text-xs text-slate-300")
            ui.space()
            self.play_btn = ui.button("Play", icon="play_arrow", on_click=self._toggle_play).props(
                "dense color=emerald"
            )
            ui.button("Step", icon="skip_next", on_click=self._step_once).props(
                "dense outline color=white"
            )
            ui.button("+0.5s", on_click=lambda: self._advance(0.5)).props(
                "dense outline color=white"
            )
            ui.button("Settle", icon="done_all", on_click=self._settle).props("dense color=sky")
            ui.button("Heal & Settle", icon="healing", on_click=self._heal_and_settle).props(
                "dense color=teal"
            )

    def _build_toolbar(self) -> None:
        with ui.card().classes("w-full p-3 bg-slate-50 border border-slate-200"):
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                scenario_opts = {s.id: s.title for s in SCENARIOS}
                self.scenario_select = (
                    ui.select(
                        scenario_opts,
                        label="Scripted scenario",
                        value=self.selected_scenario,
                        on_change=lambda e: self._load_scenario(str(e.value)),
                    )
                    .classes("w-64")
                    .props("dense outlined")
                )
                ui.button(
                    "Reload scenario",
                    icon="replay",
                    on_click=lambda: self._load_scenario(self.selected_scenario),
                ).props("dense outline")
                ui.button("Clean reset", icon="restart_alt", on_click=self._reset_clean).props(
                    "dense outline"
                )
                ui.button(
                    "Add late joiner", icon="person_add", on_click=self._add_late_joiner
                ).props("dense outline")
                ui.space()
                self.record_select = (
                    ui.select(
                        {},
                        label="Open record",
                        on_change=lambda e: self._select_record(e.value),
                    )
                    .classes("w-64")
                    .props("dense outlined")
                )
                ui.button("New record", icon="add", on_click=self._new_record_dialog).props(
                    "dense color=primary"
                )
            self.scenario_desc = ui.label().classes("text-xs text-slate-600 mt-1")

    def _build_network_and_queue_panel(self) -> None:
        with ui.row().classes("w-full items-stretch gap-3 no-wrap"):
            # Left: Network fault controls
            with ui.card().classes("w-1/2 p-3"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Network controls & fault injection").classes("font-semibold text-sm")
                    self.topology_label = ui.label().classes(
                        "font-mono text-xs bg-slate-100 px-2 py-0.5 rounded"
                    )
                with ui.row().classes("w-full items-center gap-4 mt-1"):
                    ui.label("Latency (s)").classes("text-xs w-24")
                    self.latency_slider = ui.slider(
                        min=0.05,
                        max=2.0,
                        step=0.05,
                        value=self.sim.network.latency,
                        on_change=self._on_latency_change,
                    ).classes("grow")
                    self.latency_val = ui.label("0.50s").classes("font-mono text-xs w-12")
                with ui.row().classes("w-full items-center gap-4"):
                    ui.label("Drop rate").classes("text-xs w-24")
                    self.drop_slider = ui.slider(
                        min=0.0,
                        max=1.0,
                        step=0.05,
                        value=0.0,
                        on_change=self._on_drop_change,
                    ).classes("grow")
                    self.drop_val = ui.label("0%").classes("font-mono text-xs w-12")
                with ui.row().classes("w-full items-center gap-6 mt-1"):
                    self.hold_switch = ui.switch(
                        "Hold mode (manual delivery)",
                        value=self.sim.network.hold,
                        on_change=lambda e: self._set_hold(bool(e.value)),
                    )
                    self.reorder_switch = ui.switch(
                        "Reorder links",
                        value=False,
                        on_change=lambda e: self._set_reorder(bool(e.value)),
                    )
                with ui.row().classes("w-full items-center gap-2 mt-2 flex-wrap"):
                    ui.label("Partitions:").classes("text-xs font-semibold text-slate-600")
                    ui.button("Heal all", on_click=self._heal_partition).props("dense outline")
                    ui.button(
                        "Split A | B,C",
                        on_click=lambda: self._apply_partition([["A"], ["B", "C", "D"]]),
                    ).props("dense outline")
                    ui.button(
                        "Split A,B | C",
                        on_click=lambda: self._apply_partition([["A", "B"], ["C", "D"]]),
                    ).props("dense outline")
                    ui.button(
                        "Isolate each",
                        on_click=lambda: self._apply_partition([[r] for r in self.sim.replicas]),
                    ).props("dense outline")

            # Right: In-flight / Held packet inspector
            with ui.card().classes("w-1/2 p-3"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("In-flight & held messages (manual delivery)").classes(
                        "font-semibold text-sm"
                    )
                    self.queue_count = ui.label().classes("font-mono text-xs text-slate-500")
                self.queue_box = ui.column().classes("w-full gap-1 max-h-44 overflow-y-auto mt-1")

    def _build_spacetime_panel(self) -> None:
        with ui.card().classes("w-full p-3"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Live space-time diagram").classes("font-semibold text-sm")
                with ui.row().classes("items-center gap-3"):
                    ui.switch(
                        "Show sync_req / sync_resp",
                        value=True,
                        on_change=lambda e: self._toggle_sync_arrows(bool(e.value)),
                    ).props("dense")
                    ui.button(
                        "Download SVG",
                        icon="download",
                        on_click=self._download_svg,
                    ).props("dense outline")
            self.spacetime_html = ui.html("", sanitize=False).classes("w-full mt-1")

    def _build_internals_and_logs_panel(self) -> None:
        with ui.card().classes("w-full p-3"):
            ui.label("CRDT internals (RGA element list & tombstones for open record)").classes(
                "font-semibold text-sm"
            )
            self.internals_box = ui.column().classes("w-full gap-2 mt-1")

        with ui.row().classes("w-full items-stretch gap-3 no-wrap"):
            with ui.card().classes("w-1/2 p-3"):
                ui.label("Replica trace events (newest first)").classes("font-semibold text-sm")
                self.trace_box = ui.column().classes(
                    "w-full font-mono text-xs gap-0.5 max-h-48 overflow-y-auto mt-1"
                )
            with ui.card().classes("w-1/2 p-3"):
                ui.label("Network message history (newest first)").classes("font-semibold text-sm")
                self.msg_box = ui.column().classes(
                    "w-full font-mono text-xs gap-0.5 max-h-48 overflow-y-auto mt-1"
                )

    # --------------------------------------------------------- replica cards
    def _rebuild_replica_cards(self) -> None:
        self.editors.clear()
        self.replica_row.clear()
        self.card_online: dict[str, ui.switch] = {}
        self.card_clock: dict[str, ui.label] = {}
        self.card_digest: dict[str, ui.label] = {}
        self.card_pending: dict[str, ui.column] = {}
        self.card_missing_note: dict[str, ui.label] = {}

        with self.replica_row:
            for rid, replica in self.sim.replicas.items():
                with ui.card().classes("grow min-w-[270px] p-3 border border-slate-200"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(f"Replica {rid}").classes("font-bold text-base")
                        sw = ui.switch(
                            "Online",
                            value=self.sim.network.is_online(rid),
                            on_change=lambda e, r=rid: self._set_replica_online(r, bool(e.value)),
                        ).props("dense")
                        self.card_online[rid] = sw

                    with ui.row().classes("w-full items-center justify-between gap-2"):
                        self.card_clock[rid] = ui.label().classes(
                            "font-mono text-xs text-slate-700"
                        )
                        self.card_digest[rid] = ui.label().classes(
                            "font-mono text-xs px-1.5 py-0.5 rounded"
                        )

                    self.card_missing_note[rid] = ui.label(
                        "Record not yet received on this replica"
                    ).classes("text-xs text-amber-700 italic")
                    rec_for_editor = (
                        self.active_record
                        if self.active_record and replica.has_record(self.active_record)
                        else None
                    )
                    editor = RecordEditor(replica, rec_for_editor, label=f"Text on {rid}")
                    self.editors[rid] = editor

                    ui.label("Pending causal buffer").classes(
                        "text-xs font-semibold text-slate-600 mt-1"
                    )
                    self.card_pending[rid] = ui.column().classes("w-full gap-0.5")

    # --------------------------------------------------------------- actions
    def _toggle_play(self) -> None:
        self.playing = not self.playing
        self.play_btn.set_text("Pause" if self.playing else "Play")
        self.play_btn.props(f"icon={'pause' if self.playing else 'play_arrow'}")

    def _tick(self) -> None:
        if self.playing:
            self.sim.advance(0.15)
        self._refresh_all()

    def _step_once(self) -> None:
        self.sim.step()
        self._refresh_all()

    def _advance(self, dt: float) -> None:
        self.sim.advance(dt)
        self._refresh_all()

    def _settle(self) -> None:
        self.sim.settle()
        self._refresh_all()

    def _heal_and_settle(self) -> None:
        self.sim.set_hold(False)
        self.sim.set_drop_rate(0.0)
        self.sim.set_reorder(False)
        for rid in self.sim.replicas:
            self.sim.set_online(rid, True)
        self.sim.heal()
        self.sim.settle()
        self._sync_knobs_from_sim()
        self._refresh_all()

    def _load_scenario(self, scenario_id: str) -> None:
        if scenario_id not in SCENARIOS_BY_ID:
            return
        self.playing = False
        self.play_btn.set_text("Play")
        self.play_btn.props("icon=play_arrow")
        self.selected_scenario = scenario_id
        scenario = SCENARIOS_BY_ID[scenario_id]
        self.sim = scenario.setup(self.seed)
        records = all_known_records(self.sim)
        self.active_record = records[0].id if records else None
        self._rebuild_replica_cards()
        self._sync_knobs_from_sim()
        self._refresh_all()

    def _reset_clean(self) -> None:
        self.playing = False
        self.play_btn.set_text("Play")
        self.play_btn.props("icon=play_arrow")
        self.sim = Simulation(("A", "B", "C"), seed=self.seed)
        rec = self.sim.create_record("A", "Kasese outreach", "visit_report")
        self.sim.settle()
        self.active_record = rec
        self._rebuild_replica_cards()
        self._sync_knobs_from_sim()
        self._refresh_all()

    def _add_late_joiner(self) -> None:
        for candidate in JOINER_POOL:
            if candidate not in self.sim.replicas:
                self.sim.add_replica(candidate, join=True)
                self._rebuild_replica_cards()
                self._refresh_all()
                return
        ui.notify("All 4 replicas (A-D) are already in the simulation", type="info")

    def _select_record(self, record_id: object) -> None:
        rid = str(record_id) if record_id else None
        self.active_record = rid
        self._refresh_all()

    def _new_record_dialog(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Create record on replica").classes("text-base font-semibold")
            origin = ui.select(
                list(self.sim.replicas),
                label="Origin replica",
                value=next(iter(self.sim.replicas)),
            ).classes("w-full")
            title = ui.input("Record title").classes("w-full")
            kind = ui.select(KIND_LABELS, label="Type", value="visit_report").classes("w-full")

            def create() -> None:
                clean = (title.value or "").strip()
                if not clean:
                    ui.notify("Give the record a title", type="warning")
                    return
                rec_id = self.sim.create_record(str(origin.value), clean, kind.value)
                self.active_record = rec_id
                dialog.close()
                self._refresh_all()

            with ui.row().classes("mt-2"):
                ui.button("Create", on_click=create)
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    def _set_replica_online(self, rid: str, online: bool) -> None:
        self.sim.set_online(rid, online)
        self._refresh_all()

    def _on_latency_change(self, event: object) -> None:
        val = float(getattr(event, "value", 0.5))
        self.sim.network.latency = val
        self.latency_val.set_text(f"{val:.2f}s")

    def _on_drop_change(self, event: object) -> None:
        val = float(getattr(event, "value", 0.0))
        self.sim.set_drop_rate(val)
        self.drop_val.set_text(f"{val * 100:.0f}%")

    def _set_hold(self, on: bool) -> None:
        self.sim.set_hold(on)
        self._refresh_all()

    def _set_reorder(self, on: bool) -> None:
        self.sim.set_reorder(on)
        self._refresh_all()

    def _heal_partition(self) -> None:
        self.sim.heal()
        self._refresh_all()

    def _apply_partition(self, groups: list[list[str]]) -> None:
        present = set(self.sim.replicas)
        filtered = [[r for r in g if r in present] for g in groups]
        filtered = [g for g in filtered if g]
        if filtered:
            self.sim.partition(filtered)
        self._refresh_all()

    def _deliver_msg(self, msg_id: int) -> None:
        self.sim.deliver(msg_id)
        self._refresh_all()

    def _drop_msg(self, msg_id: int) -> None:
        self.sim.drop(msg_id)
        self._refresh_all()

    def _toggle_sync_arrows(self, include: bool) -> None:
        self.include_sync = include
        self._refresh_all()

    def _download_svg(self) -> None:
        svg = render_spacetime_svg(self.sim, include_sync=self.include_sync)
        ui.download.content(svg, "fieldsync-spacetime.svg", "image/svg+xml")

    def _sync_knobs_from_sim(self) -> None:
        self.latency_slider.set_value(self.sim.network.latency)
        self.latency_val.set_text(f"{self.sim.network.latency:.2f}s")
        links = [
            self.sim.network.link(a, b)
            for a in self.sim.replicas
            for b in self.sim.replicas
            if a != b
        ]
        drop = links[0].drop_rate if links else 0.0
        reorder = any(link.reorder for link in links)
        self.drop_slider.set_value(drop)
        self.drop_val.set_text(f"{drop * 100:.0f}%")
        self.hold_switch.set_value(self.sim.network.hold)
        self.reorder_switch.set_value(reorder)

    # --------------------------------------------------------------- refresh
    def _refresh_all(self) -> None:
        self.clock_badge.set_text(f"t = {self.sim.now:.2f}s")
        text, cls = convergence_status(self.sim)
        self.conv_badge.set_text(text)
        self.conv_badge.classes(replace=f"font-mono text-xs font-bold px-2.5 py-1 rounded {cls}")
        in_flight = self.sim.network.in_flight()
        held = self.sim.network.held()
        self.inflight_badge.set_text(f"in-flight {len(in_flight)} · held {len(held)}")
        self.topology_label.set_text(format_groups(self.sim))

        scenario = SCENARIOS_BY_ID.get(self.selected_scenario)
        if scenario is not None:
            self.scenario_desc.set_text(scenario.description)

        # Record selector options
        records = all_known_records(self.sim)
        rec_opts = {r.id: f"{r.title} ({KIND_LABELS[r.kind]} · {r.id})" for r in records}
        if self.active_record not in rec_opts:
            self.active_record = records[0].id if records else None
        self.record_select.set_options(rec_opts, value=self.active_record)

        # Update each replica card
        converged = self.sim.converged()
        for rid, replica in self.sim.replicas.items():
            self.card_online[rid].set_value(self.sim.network.is_online(rid))
            self.card_clock[rid].set_text(f"clock {replica.clock}")
            has_rec = self.active_record is not None and replica.has_record(self.active_record)
            rec_dig = replica.digest(self.active_record) if has_rec and self.active_record else "-"
            dig_cls = (
                "bg-emerald-100 text-emerald-900" if converged else "bg-amber-100 text-amber-900"
            )
            self.card_digest[rid].set_text(f"rec {rec_dig} · state {replica.state_digest()}")
            self.card_digest[rid].classes(
                replace=f"font-mono text-xs px-1.5 py-0.5 rounded {dig_cls}"
            )
            self.card_missing_note[rid].set_visibility(
                self.active_record is not None and not has_rec
            )
            editor = self.editors[rid]
            target_rec = self.active_record if has_rec else None
            if editor.record != target_rec:
                editor.set_record(target_rec)
            else:
                editor.refresh()

            # Pending causal buffer
            p_box = self.card_pending[rid]
            p_box.clear()
            pending_lines = format_pending(replica)
            with p_box:
                if not pending_lines:
                    ui.label("Empty (0 waiting)").classes("text-xs text-slate-400 font-mono")
                else:
                    for line in pending_lines:
                        ui.label(line).classes(
                            "text-xs font-mono bg-amber-100 text-amber-900 px-2 py-0.5 rounded"
                        )

        # In-flight / Held queue
        self.queue_count.set_text(f"{len(in_flight)} active")
        self.queue_box.clear()
        with self.queue_box:
            if not in_flight:
                ui.label("No packets currently in flight or held.").classes(
                    "text-xs text-slate-400 italic"
                )
            for msg in in_flight:
                with ui.row().classes(
                    "w-full items-center justify-between bg-slate-50 px-2 py-1 rounded border"
                ):
                    ui.label(
                        f"#{msg.id} {msg.src}->{msg.dst} [{msg.status}] {msg.envelope.summary()}"
                    ).classes("font-mono text-xs")
                    with ui.row().classes("gap-1"):
                        ui.button(
                            "Deliver",
                            on_click=lambda _, mid=msg.id: self._deliver_msg(mid),
                        ).props("dense size=sm color=emerald")
                        ui.button(
                            "Drop",
                            on_click=lambda _, mid=msg.id: self._drop_msg(mid),
                        ).props("dense size=sm color=red outline")

        # Space-time diagram SVG
        self.spacetime_html.set_content(
            render_spacetime_svg(self.sim, include_sync=self.include_sync)
        )

        # CRDT Internals view
        self.internals_box.clear()
        with self.internals_box:
            if self.active_record is None:
                ui.label("No record selected").classes("text-xs text-slate-400")
            else:
                for rid, replica in self.sim.replicas.items():
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(f"Replica {rid}:").classes("font-mono text-xs font-bold w-20")
                        if replica.has_record(self.active_record):
                            elems = replica.document(self.active_record).elements()
                            ui.html(render_elements_html(elems), sanitize=False).classes("grow")
                        else:
                            ui.label("Record not created/received yet").classes(
                                "text-xs text-slate-400 italic"
                            )

        # Trace & message logs
        self.trace_box.clear()
        with self.trace_box:
            for ev in reversed(self.sim.trace[-MAX_LOG_ITEMS:]):
                ui.label(format_trace_event(ev))

        self.msg_box.clear()
        with self.msg_box:
            for msg in reversed(self.sim.messages()[-MAX_LOG_ITEMS:]):
                ui.label(format_inflight(msg))


def register(*, seed: int = 0) -> None:
    @ui.page("/")
    def index() -> None:
        SimPage(seed=seed)
