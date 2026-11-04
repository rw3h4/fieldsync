"""End-to-end tests: real relay and node processes, driven in a real browser (Playwright).

Skipped unless Playwright's Chromium is installed (``uv run playwright install chromium``),
so CI and machines without browsers are unaffected. Run with ``uv run pytest -m e2e``.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="module")  # closed before other tests: Playwright keeps a loop running
def browser() -> Iterator[object]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Error:
            pytest.skip("Playwright Chromium not installed (uv run playwright install chromium)")
        yield browser
        browser.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


@pytest.fixture
def two_nodes(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A relay and two editor nodes as separate processes; yields the two page URLs."""
    relay_port, port1, port2 = free_port(), free_port(), free_port()
    relay_url = f"ws://127.0.0.1:{relay_port}"
    relay = ["fieldsync.net.relay", "--id", "relay_e2e", "--port", str(relay_port)]

    def node(node_id: str, port: int) -> list[str]:
        data = str(tmp_path / f"{node_id}.jsonl")
        args = ["--id", node_id, "--port", str(port), "--relays", relay_url, "--data-file", data]
        return ["fieldsync.ui.node.app", *args]

    commands = [relay, node("node1", port1), node("node2", port2)]
    # NiceGUI switches to its own test mode if it sees pytest's environment variables.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "NICEGUI"))}
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", *cmd, "--host", "127.0.0.1"],
            stdout=(tmp_path / f"proc{i}.log").open("w"),
            stderr=subprocess.STDOUT,
            env=env,
        )
        for i, cmd in enumerate(commands)
    ]
    try:
        urls = [f"http://127.0.0.1:{port1}", f"http://127.0.0.1:{port2}"]
        for url in urls:
            wait_for_http(url)
        yield urls[0], urls[1]
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
