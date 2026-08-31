"""End-to-end loop behavior through the CLI, offline via the mock runtime."""
import json
import os

from conftest import DEMO, cli, last_run_id, run_dir


def journal(home, rid):
    path = os.path.join(run_dir(home, rid), "journal.jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_full_loop_auto_promotes_after_feedback_iteration(home):
    out = cli(home, "run", os.path.join(DEMO, "slugify_auto.json"),
              "--harness", "mock").stdout
    assert "finished: succeeded" in out
    events = journal(home, last_run_id(home))
    generated = [e for e in events if e["type"] == "GENERATED"]
    assert len(generated) == 2  # first attempt fails, feedback fixes it
    assert any(e["type"] == "HOLDOUT_VERIFIED" and e["payload"]["passed"]
               for e in events)
    manifest = json.load(open(
        os.path.join(home, "artifacts", "slugify-auto", "manifest.json"),
        encoding="utf-8"))
    assert manifest["current"] == 1


def test_step_budget_then_resume_reaches_hitl_and_approval_succeeds(home):
    cli(home, "run", os.path.join(DEMO, "slugify.json"),
        "--harness", "mock", "--step", "2")
    rid = last_run_id(home)
    out = cli(home, "resume", rid).stdout
    assert "waiting for human decision" in out
    out = cli(home, "approve", rid).stdout
    assert "finished: succeeded" in out
    decisions = [e for e in journal(home, rid) if e["type"] == "HITL_DECISION"]
    assert decisions and decisions[-1]["payload"]["actor"]
    assert decisions[-1]["payload"]["candidate_hash"]


def test_reject_note_feeds_back_into_next_iteration(home):
    cli(home, "run", os.path.join(DEMO, "slugify.json"), "--harness", "mock")
    rid = last_run_id(home)
    cli(home, "reject", rid, "--note", "please reconsider the approach")
    instructions = open(
        os.path.join(run_dir(home, rid), "workspace", "instructions.md"),
        encoding="utf-8").read()
    assert "please reconsider the approach" in instructions
    out = cli(home, "approve", rid).stdout
    assert "finished: succeeded" in out


def test_runtime_failure_is_failure_not_fake_success(home):
    # Point the "dsh" harness at a command that exits nonzero: the run must
    # fail loudly, never fall back to any simulated result.
    py = os.environ.get("PYTHON", "python")
    env = {"LOOPGRAPH_DSH_CMD": f"{py} -c \"import sys; sys.exit(3)\"",
           "LOOPGRAPH_DSH_VERSION_CMD": f"{py} --version"}
    out = cli(home, "run", os.path.join(DEMO, "slugify_auto.json"),
              "--harness", "dsh", env=env).stdout
    assert "finished: failed" in out
    assert "harness invocation failed" in out
