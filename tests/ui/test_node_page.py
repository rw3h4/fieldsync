from fieldsync.net.client import ClientStatus, PersistError
from fieldsync.ui.node.page import persist_banner


def test_no_banner_while_writes_work() -> None:
    assert persist_banner(ClientStatus(state="connected"), started_at=100.0) is None


def test_banner_shows_message_and_time_since_node_start() -> None:
    status = ClientStatus(persist_error=PersistError("[Errno 28] No space left on device", 112.5))
    assert persist_banner(status, started_at=100.0) == (
        "DATA FILE WRITE FAILING since 12.5s: [Errno 28] No space left on device"
    )
