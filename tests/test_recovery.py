"""Durable-recovery boundaries: unknown outcomes park, humans resolve."""
import json
import os

from conftest import DEMO, cli, last_run_id, run_dir

FIXED = (
    "import re\n\n"
    "def slugify(text):\n"
    '    text = re.sub(r"[^a-z0-9]+", "-", text.lower())\n'
    '    return text.strip("-")\n'
)


def crash_run(home):
    proc = cli(home, "run", os.path.join(DEMO, "slugify.json"),
               "--harness", "mock",
               env={"LOOPGRAPH_CRASH_AFTER_INTENT": "1"}, check=False)
    assert proc.returncode == 70
    return last_run_id(home)


def test_crash_mid_effect_parks_and_resume_never_retries(home):
    rid = crash_run(home)
    assert "unknown_outcome" in cli(home, "status", rid).stdout
    out = cli(home, "resume", rid).stdout
    assert "NOT retrying automatically" in out
    # still parked: a second resume behaves identically
    assert "NOT retrying automatically" in cli(home, "resume", rid).stdout


def test_resolution_not_executed_allows_rerun(home):
    rid = crash_run(home)
    out = cli(home, "resolve-effect", rid, "--outcome", "not-executed",
              "--note", "workspace empty, runtime never started").stdout
    assert "waiting for human decision" in out
    events = [json.loads(line) for line in open(
        os.path.join(run_dir(home, rid), "journal.jsonl"), encoding="utf-8")]
    resolved = [e for e in events if e["type"] == "EFFECT_RESOLVED"]
    assert resolved and resolved[0]["payload"]["actor"]


def test_resolution_completed_recovers_workspace_artifact(home):
    rid = crash_run(home)
    # Simulate: the runtime actually finished its work before the crash.
    with open(os.path.join(run_dir(home, rid), "workspace", "candidate.py"),
              "w", encoding="utf-8") as f:
        f.write(FIXED)
    out = cli(home, "resolve-effect", rid, "--outcome", "completed").stdout
    assert "waiting for human decision" in out  # verified without re-running
    events = [json.loads(line) for line in open(
        os.path.join(run_dir(home, rid), "journal.jsonl"), encoding="utf-8")]
    generated = [e for e in events if e["type"] == "GENERATED"]
    assert len(generated) == 1 and generated[0]["payload"].get("recovered")


def test_resolution_completed_without_artifact_is_refused(home):
    rid = crash_run(home)
    proc = cli(home, "resolve-effect", rid, "--outcome", "completed",
               check=False)
    assert proc.returncode != 0


def test_semantic_spec_tamper_refuses_to_drive(home):
    cli(home, "run", os.path.join(DEMO, "slugify.json"),
        "--harness", "mock", "--step", "1")
    rid = last_run_id(home)
    frozen = os.path.join(run_dir(home, rid), "task.json")
    with open(frozen, encoding="utf-8") as f:
        content = f.read()
    with open(frozen, "w", encoding="utf-8") as f:
        f.write(content.replace("hello-world", "HELLO-WORLD"))
    proc = cli(home, "resume", rid, check=False)
    assert proc.returncode != 0
    assert "immutable" in (proc.stdout + proc.stderr)
