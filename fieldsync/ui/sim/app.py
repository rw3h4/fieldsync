"""``uv run sim``: start the interactive FieldSync simulator UI."""

import argparse
from collections.abc import Sequence

from nicegui import ui

from fieldsync.ui.sim.page import register


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sim", description="FieldSync simulator UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="web UI port (default: 8080)")
    parser.add_argument("--seed", type=int, default=0, help="PRNG seed for reproducibility")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    register(seed=args.seed)
    ui.run(
        host=args.host,
        port=args.port,
        title="FieldSync · Simulator",
        reload=False,
        show=False,
        show_welcome_message=False,
    )


if __name__ == "__main__":
    main()
