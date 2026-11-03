"""``uv run node``: start one editor node (headless core + NiceGUI page) on one event loop."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from nicegui import app, ui

from fieldsync.net.node import Node
from fieldsync.ui.node.page import register


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="node", description="FieldSync editor node")
    parser.add_argument(
        "--id",
        required=True,
        help="node id, unique per device (letters, digits, _)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081, help="port of this node's web page")
    parser.add_argument(
        "--relays",
        default="ws://localhost:8765,ws://localhost:8766",
        help="comma-separated relay URLs in failover order",
    )
    parser.add_argument("--data-file", type=Path, help="JSON Lines file for this node's replica")
    parser.add_argument("--anti-entropy", type=float, default=2.0, help="sync interval (s)")
    parser.add_argument("--heartbeat", type=float, default=1.0, help="ping interval (s)")
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    node = Node(
        args.id,
        [u.strip() for u in args.relays.split(",") if u.strip()],
        data_file=args.data_file,
        anti_entropy_interval=args.anti_entropy,
        heartbeat_interval=args.heartbeat,
        heartbeat_timeout=args.heartbeat_timeout,
    )
    # Node runs on NiceGUI's event loop: no threads, the replica is only touched from this loop.
    app.on_startup(node.start)
    app.on_shutdown(node.stop)
    register(node)
    ui.run(
        host=args.host,
        port=args.port,
        title=f"FieldSync · {args.id}",
        reload=False,
        show=False,
        show_welcome_message=False,
    )


if __name__ == "__main__":
    main()
