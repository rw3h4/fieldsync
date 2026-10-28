from fieldsync.sim import Simulation


def shared_record(s: Simulation, creator: str = "A") -> str:
    """Create one record on ``creator`` and let it reach every replica before the test starts."""
    rec = s.create_record(creator, "notes", "case_note")
    assert s.settle()
    return rec
