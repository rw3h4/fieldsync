"""End-to-end property: random edits under random network faults always converge after heal."""

from hypothesis import given, settings
from hypothesis import strategies as st

from fieldsync.sim import Simulation

from .conftest import shared_record

IDS = ["A", "B", "C", "D"]


@settings(max_examples=100, deadline=None)
@given(st.data())
def test_random_faults_then_heal_converges(data: st.DataObject) -> None:
    n = data.draw(st.integers(2, 4), label="replicas")
    ids = IDS[:n]
    s = Simulation(
        ids,
        seed=data.draw(st.integers(0, 2**16), label="seed"),
        latency=0.5,
        jitter=0.4,
        anti_entropy_interval=data.draw(st.sampled_from([None, 1.0, 3.0]), label="ae"),
    )
    rec = shared_record(s)
    s.set_reorder(data.draw(st.booleans(), label="reorder"))

    for _ in range(data.draw(st.integers(1, 30), label="steps")):
        action = data.draw(
            st.sampled_from(
                ["edit", "edit", "edit", "advance", "partition", "heal", "loss", "offline"]
            ),
            label="action",
        )
        rid = data.draw(st.sampled_from(ids), label="replica")
        if action == "edit":
            length = len(s[rid].text(rec))
            if length and data.draw(st.booleans(), label="delete?"):
                index = data.draw(st.integers(0, length - 1))
                s.edit(rid, rec, index, delete=data.draw(st.integers(1, min(3, length - index))))
            else:
                index = data.draw(st.integers(0, length))
                s.edit(rid, rec, index, insert=data.draw(st.text("abxy", min_size=1, max_size=3)))
        elif action == "advance":
            s.advance(data.draw(st.floats(0.0, 3.0)))
        elif action == "partition":
            cut = data.draw(st.integers(1, n - 1))
            shuffled = data.draw(st.permutations(ids))
            s.partition([shuffled[:cut], shuffled[cut:]])
        elif action == "heal":
            s.heal()
        elif action == "loss":
            s.set_drop_rate(data.draw(st.sampled_from([0.0, 0.3, 1.0])))
        elif action == "offline":
            s.set_online(rid, not s.network.is_online(rid))

    # Restore connectivity; replicas resync via anti-entropy on reconnect.
    s.set_drop_rate(0.0)
    for rid in ids:
        s.set_online(rid, True)
    s.heal()
    assert s.settle(max_time=300)
    assert s.converged()
    assert len({r.document(rec).snapshot() for r in s.replicas.values()}) == 1
