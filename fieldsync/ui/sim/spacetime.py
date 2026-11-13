"""Pure-Python SVG generator for the live simulator space-time diagram.

Draws one horizontal timeline per replica across virtual time ``t``, diagonal
arrows for network messages (delivered, dropped, in-flight, held), and coloured
dots for local/remote trace events with hover tooltips. Returns a standalone
SVG string suitable both for inline rendering in NiceGUI and for direct
download as a ``.svg`` figure for the report.
"""

from html import escape

from fieldsync.runtime.trace import EventKind, TraceEvent
from fieldsync.sim.network import InFlight
from fieldsync.sim.simulation import Simulation

EVENT_COLORS: dict[EventKind, str] = {
    "create": "#0284c7",  # sky-600
    "edit": "#2563eb",  # blue-600
    "apply": "#16a34a",  # green-600
    "buffer": "#d97706",  # amber-600
    "duplicate": "#64748b",  # slate-500
    "send": "#94a3b8",  # slate-400
    "receive": "#94a3b8",  # slate-400
}

VISIBLE_EVENTS: frozenset[EventKind] = frozenset({"create", "edit", "apply", "buffer", "duplicate"})


def _max_time(sim: Simulation) -> float:
    horizon = max(sim.now, 1.0)
    for msg in sim.messages():
        if msg.ended_at is not None:
            horizon = max(horizon, msg.ended_at)
        if msg.deliver_at is not None:
            horizon = max(horizon, msg.deliver_at)
    for ev in sim.trace:
        horizon = max(horizon, ev.time)
    return horizon * 1.05


def _render_message(
    msg: InFlight,
    lane_y: dict[str, float],
    x_of: callable,
    now: float,
) -> str:
    if msg.src not in lane_y or msg.dst not in lane_y:
        return ""
    y1 = lane_y[msg.src]
    y2 = lane_y[msg.dst]
    x1 = x_of(msg.sent_at)
    is_sync = msg.envelope.kind in ("sync_req", "sync_resp")
    stroke_w = "1.3" if is_sync else "2.0"
    tip = escape(
        f"#{msg.id} {msg.src}->{msg.dst} ({msg.status}): {msg.envelope.summary()}",
        quote=True,
    )

    if msg.status == "delivered":
        end_t = msg.ended_at if msg.ended_at is not None else msg.sent_at
        x2 = x_of(end_t)
        dash = ' stroke-dasharray="3,3"' if is_sync else ""
        return (
            f'<g class="msg msg-delivered"><title>{tip}</title>'
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#16a34a" stroke-width="{stroke_w}"{dash} '
            f'marker-end="url(#arrow-delivered)" opacity="0.85"/></g>'
        )

    if msg.status == "dropped":
        end_t = msg.ended_at if msg.ended_at is not None else msg.sent_at
        x2 = x_of(end_t)
        xm = x1 + (x2 - x1) * 0.65
        ym = y1 + (y2 - y1) * 0.65
        reason = escape(msg.drop_reason or "dropped", quote=True)
        return (
            f'<g class="msg msg-dropped"><title>{tip} [{reason}]</title>'
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{xm:.1f}" y2="{ym:.1f}" '
            f'stroke="#dc2626" stroke-width="{stroke_w}" stroke-dasharray="4,3"/>'
            f'<text x="{xm:.1f}" y="{ym + 4:.1f}" text-anchor="middle" '
            f'font-size="11" font-weight="bold" fill="#dc2626">✕</text></g>'
        )

    if msg.status == "held":
        ym = y1 + (14.0 if y2 > y1 else -14.0)
        return (
            f'<g class="msg msg-held"><title>{tip}</title>'
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x1 + 14:.1f}" y2="{ym:.1f}" '
            f'stroke="#d97706" stroke-width="2" stroke-dasharray="2,2"/>'
            f'<circle cx="{x1 + 14:.1f}" cy="{ym:.1f}" r="4.5" '
            f'fill="#fef3c7" stroke="#d97706" stroke-width="1.8"/></g>'
        )

    # status == "in_flight"
    target_t = msg.deliver_at if msg.deliver_at is not None else now + 0.5
    x2 = x_of(target_t)
    span = max(target_t - msg.sent_at, 1e-6)
    frac = min(max((now - msg.sent_at) / span, 0.0), 1.0)
    xc = x1 + (x2 - x1) * frac
    yc = y1 + (y2 - y1) * frac
    return (
        f'<g class="msg msg-in-flight"><title>{tip}</title>'
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="#f59e0b" stroke-width="{stroke_w}" stroke-dasharray="4,4" '
        f'marker-end="url(#arrow-inflight)" opacity="0.8"/>'
        f'<circle cx="{xc:.1f}" cy="{yc:.1f}" r="3.5" fill="#f59e0b"/></g>'
    )


def _render_event(ev: TraceEvent, y: float, x: float, offset_y: float) -> str:
    color = EVENT_COLORS.get(ev.kind, "#475569")
    cy = y + offset_y
    tip = escape(
        f"t={ev.time:.2f}s [{ev.replica}] {ev.kind}: {ev.detail} | clock {ev.clock}",
        quote=True,
    )
    return (
        f'<g class="trace-event trace-{ev.kind}"><title>{tip}</title>'
        f'<circle cx="{x:.1f}" cy="{cy:.1f}" r="4.5" '
        f'fill="{color}" stroke="#ffffff" stroke-width="1.2"/></g>'
    )


def render_spacetime_svg(
    sim: Simulation,
    *,
    width: int = 860,
    height: int = 260,
    include_sync: bool = True,
) -> str:
    """Render ``sim``'s replicas, messages and trace events as a standalone SVG."""
    replica_ids = list(sim.replicas)
    n_lanes = max(len(replica_ids), 1)
    pad_left = 70.0
    pad_right = 28.0
    pad_top = 36.0
    pad_bottom = 38.0
    plot_w = max(width - pad_left - pad_right, 100.0)
    plot_h = max(height - pad_top - pad_bottom, 60.0)
    horizon = _max_time(sim)

    def x_of(t: float) -> float:
        return pad_left + (min(max(t, 0.0), horizon) / horizon) * plot_w

    lane_y: dict[str, float] = {}
    for idx, rid in enumerate(replica_ids):
        if n_lanes == 1:
            lane_y[rid] = pad_top + plot_h / 2.0
        else:
            lane_y[rid] = pad_top + (idx / (n_lanes - 1)) * plot_h

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="{height}" '
        f' font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">',
        "<defs>"
        '<marker id="arrow-delivered" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#16a34a"/></marker>'
        '<marker id="arrow-inflight" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#f59e0b"/></marker>'
        "</defs>",
        f'<rect width="{width}" height="{height}" rx="8" fill="#f8fafc" '
        f'stroke="#e2e8f0" stroke-width="1"/>',
    ]

    # Legend at the top
    legend_items = [
        ("#2563eb", "create/edit"),
        ("#16a34a", "delivered/apply"),
        ("#d97706", "buffered/held"),
        ("#dc2626", "dropped (✕)"),
    ]
    lx = pad_left
    for color, label in legend_items:
        parts.append(
            f'<circle cx="{lx:.1f}" cy="16" r="4" fill="{color}"/>'
            f'<text x="{lx + 8:.1f}" y="20" font-size="10" fill="#475569">'
            f"{escape(label)}</text>"
        )
        lx += 125.0

    # Time grid ticks along bottom
    for step in range(5):
        frac = step / 4.0
        t_val = horizon * frac
        tx = pad_left + frac * plot_w
        parts.append(
            f'<line x1="{tx:.1f}" y1="{pad_top - 6:.1f}" '
            f'x2="{tx:.1f}" y2="{height - pad_bottom + 6:.1f}" '
            f'stroke="#e2e8f0" stroke-width="1" stroke-dasharray="2,2"/>'
            f'<text x="{tx:.1f}" y="{height - 10:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#64748b">{t_val:.1f}s</text>'
        )

    # Current virtual clock cursor
    now_x = x_of(sim.now)
    parts.append(
        f'<line x1="{now_x:.1f}" y1="{pad_top - 8:.1f}" '
        f'x2="{now_x:.1f}" y2="{height - pad_bottom + 4:.1f}" '
        f'stroke="#0f172a" stroke-width="1.2" stroke-dasharray="3,2" opacity="0.5"/>'
    )

    # Replica swimlanes
    for rid in replica_ids:
        y = lane_y[rid]
        online = sim.network.is_online(rid)
        lane_color = "#94a3b8" if online else "#fca5a5"
        label_color = "#0f172a" if online else "#dc2626"
        parts.append(
            f'<line x1="{pad_left:.1f}" y1="{y:.1f}" '
            f'x2="{width - pad_right:.1f}" y2="{y:.1f}" '
            f'stroke="{lane_color}" stroke-width="1.5"/>'
            f'<text x="{pad_left - 12:.1f}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" font-weight="bold" fill="{label_color}">'
            f"{escape(rid)}</text>"
        )

    # Message arrows
    for msg in sim.messages():
        if not include_sync and msg.envelope.kind in ("sync_req", "sync_resp"):
            continue
        rendered = _render_message(msg, lane_y, x_of, sim.now)
        if rendered:
            parts.append(rendered)

    # Trace event dots (staggered slightly if multiple events share a timestamp on a lane)
    seen_buckets: dict[tuple[str, int], int] = {}
    for ev in sim.trace:
        if ev.kind not in VISIBLE_EVENTS or ev.replica not in lane_y:
            continue
        bucket = (ev.replica, round(ev.time * 20))
        idx_in_bucket = seen_buckets.get(bucket, 0)
        seen_buckets[bucket] = idx_in_bucket + 1
        offset_y = (idx_in_bucket % 3 - 1) * 5.0 if idx_in_bucket else 0.0
        parts.append(_render_event(ev, lane_y[ev.replica], x_of(ev.time), offset_y))

    parts.append("</svg>")
    return "\n".join(parts)
