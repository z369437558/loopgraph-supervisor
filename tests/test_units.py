"""Unit tests for the pure pieces: spec identity, state reducer, versions."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loopgraph.spec import SpecStore, spec_hash, validate  # noqa: E402
from loopgraph.state import replay  # noqa: E402
from loopgraph.versions import VersionStore  # noqa: E402

SPEC = {
    "task_id": "t", "goal": "g", "entry_point": "f",
    "tests": [{"input": "a", "expected": "b"}],
    "graph": {"entry": "generate", "nodes": {
        "generate": {"type": "agent", "next": "verify"},
        "verify": {"type": "verify", "on_pass": "promote",
                   "on_fail": "generate"},
        "promote": {"type": "promote"},
    }},
}


def ev(t, **p):
    return {"seq": 0, "ts": "", "type": t, "payload": p}


def test_spec_hash_is_content_addressed_not_formatting_addressed():
    reordered = dict(reversed(list(SPEC.items())))
    assert spec_hash(SPEC) == spec_hash(reordered)
    changed = {**SPEC, "goal": "different"}
    assert spec_hash(SPEC) != spec_hash(changed)


def test_spec_registry_is_append_only(tmp_path):
    store = SpecStore(str(tmp_path))
    h1 = store.register(SPEC)
    h2 = store.register(SPEC)  # same content -> same revision, no duplicate
    assert h1 == h2
    assert [r["hash"] for r in store.revisions("t")] == [h1]
    h3 = store.register({**SPEC, "goal": "v2"})
    assert h3 != h1
    assert len(store.revisions("t")) == 2


def test_spec_validation_rejects_broken_graphs():
    with pytest.raises(SystemExit):
        validate({**SPEC, "graph": {"entry": "nope", "nodes": {}}})
    bad_edge = {**SPEC, "graph": {"entry": "generate", "nodes": {
        "generate": {"type": "agent", "next": "missing"}}}}
    with pytest.raises(SystemExit):
        validate(bad_edge)


def test_reducer_derives_unknown_outcome_from_dangling_intent():
    base = [ev("RUN_STARTED", entry_node="generate", spec_hash="h"),
            ev("NODE_STARTED", node="generate"),
            ev("EFFECT_INTENT", effect_id="e1", kind="harness.run_task",
               harness="mock", node="generate", iteration=1)]
    assert replay(base)["status"] == "unknown_outcome"
    resolved = base + [ev("EFFECT_RESOLVED", effect_id="e1",
                          outcome="not-executed", actor="x")]
    assert replay(resolved)["status"] == "running"
    completed = base + [ev("EFFECT_RESULT", effect_id="e1", ok=True,
                           iteration=1, meta={})]
    assert replay(completed)["status"] == "running"
    assert replay(completed)["pending_effect"] is None


def test_reducer_holdout_failure_resets_verified_with_generic_feedback():
    events = [ev("RUN_STARTED", entry_node="generate", spec_hash="h"),
              ev("VERIFIED", passed=True, feedback=None, results=[]),
              ev("HOLDOUT_VERIFIED", passed=False, results=[],
                 detail="secret detail", agent_feedback="generic only")]
    s = replay(events)
    assert s["verified"] is False
    assert s["feedback"] == "generic only"


def test_version_store_rollback_and_idempotent_promotion(tmp_path):
    store = VersionStore(str(tmp_path), "t")
    src = tmp_path / "cand.py"
    src.write_text("x = 1\n")
    assert store.promote(str(src), "run-a") == 1
    assert store.promote(str(src), "run-a") == 1  # crash-recovery replay
    assert store.promote(str(src), "run-b") == 2
    assert store.rollback() == 1
    m = store.manifest()
    assert m["current"] == 1
    assert [e["action"] for e in m["log"]] == ["promote", "promote", "rollback"]
    with pytest.raises(SystemExit):
        store.rollback(to=99)
