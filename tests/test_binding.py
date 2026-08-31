"""Authorization boundaries: approvals bind to content; holdout gates
promotion without leaking into the agent loop."""
import json
import os

from conftest import DEMO, cli, last_run_id, run_dir


def test_stale_approval_is_refused_after_candidate_changes(home):
    cli(home, "run", os.path.join(DEMO, "slugify.json"), "--harness", "mock")
    rid = last_run_id(home)
    cand = os.path.join(run_dir(home, rid), "workspace", "candidate.py")
    with open(cand, encoding="utf-8") as f:
        original = f.read()
    with open(cand, "w", encoding="utf-8") as f:
        f.write("def slugify(text):\n    return 'malicious'\n")
    proc = cli(home, "approve", rid, check=False)
    assert proc.returncode != 0
    assert "candidate changed" in (proc.stdout + proc.stderr)
    with open(cand, "w", encoding="utf-8") as f:
        f.write(original)
    assert "finished: succeeded" in cli(home, "approve", rid).stdout


def test_holdout_failure_loops_generically_and_never_leaks(home, tmp_path):
    # A holdout the mock runtime's best answer cannot satisfy: the run must
    # end failed (bounded by max_iterations), and the hidden case must never
    # appear in anything the agent reads.
    with open(os.path.join(DEMO, "slugify_auto.json"), encoding="utf-8") as f:
        spec = json.load(f)
    spec["task_id"] = "slugify-impossible"
    spec["max_iterations"] = 3
    spec["holdout_tests"] = [
        {"input": "keep_underscores", "expected": "keep_underscores"}]
    spec_path = str(tmp_path / "impossible.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    out = cli(home, "run", spec_path, "--harness", "mock").stdout
    assert "finished: failed" in out
    assert "holdout" in out
    rid = last_run_id(home)
    instructions = open(
        os.path.join(run_dir(home, rid), "workspace", "instructions.md"),
        encoding="utf-8").read()
    assert "keep_underscores" not in instructions
    assert "hidden holdout evaluation" in instructions  # generic message only
    assert not os.path.exists(
        os.path.join(home, "artifacts", "slugify-impossible", "manifest.json"))
