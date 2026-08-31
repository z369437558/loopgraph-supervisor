"""LoopGraph supervisor: walks the task's node graph, journaling every step.

The drive loop never holds state that is not already in the journal, so the
process can be killed between any two node executions and `resume` continues
exactly where it stopped. Node execution is at-least-once: a crash inside a
node re-executes that node on resume (generation and verification are safe to
repeat; promotion appends a new immutable version, never overwrites).
"""
import json
import os
import shutil
import time
import uuid

from .harness import make_harness, resolve_harness_name
from .journal import Journal
from .state import replay
from .versions import VersionStore
from . import verify as verifier

ROOT = os.environ.get(
    "LOOPGRAPH_HOME",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
RUNS_DIR = os.path.join(ROOT, "runs")

SYSTEM_PROMPT = (
    "You are a coding agent inside an automated improve-verify loop. "
    "Reply with a single fenced ```python code block containing the complete, "
    "self-contained implementation, and nothing else."
)


class Run:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = os.path.join(RUNS_DIR, run_id)
        self.journal = Journal(self.dir)

    @classmethod
    def create(cls, task_path: str, harness_name: str) -> "Run":
        with open(task_path, encoding="utf-8") as f:
            task = json.load(f)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run = cls(run_id)
        os.makedirs(run.dir, exist_ok=True)
        # The run directory is self-contained: task spec, meta, journal,
        # candidates. Copy the task in so later edits to the original file
        # cannot change a run's semantics mid-flight.
        shutil.copyfile(task_path, run.task_path)
        with open(os.path.join(run.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"harness": harness_name, "task_id": task["task_id"]}, f)
        run.journal.append("RUN_STARTED", task_id=task["task_id"],
                           entry_node=task["graph"]["entry"],
                           harness=harness_name)
        return run

    @property
    def task_path(self) -> str:
        return os.path.join(self.dir, "task.json")

    @property
    def task(self) -> dict:
        with open(self.task_path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def meta(self) -> dict:
        with open(os.path.join(self.dir, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    @property
    def candidate_path(self) -> str:
        return os.path.join(self.dir, "candidate.py")

    def state(self) -> dict:
        return replay(self.journal.read())

    # -- external control plane (pause requests land here, applied between nodes)
    @property
    def control_path(self) -> str:
        return os.path.join(self.dir, "control.json")

    def control(self) -> dict:
        if os.path.exists(self.control_path):
            with open(self.control_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def set_control(self, **kv):
        c = self.control()
        c.update(kv)
        with open(self.control_path, "w", encoding="utf-8") as f:
            json.dump(c, f)


def extract_code(text: str) -> str:
    if "```" in text:
        block = text.split("```", 2)[1]
        if block.lstrip().startswith("python"):
            block = block.lstrip()[len("python"):]
        return block.strip() + "\n"
    return text.strip() + "\n"


def build_prompt(task: dict, state: dict) -> str:
    entry = task["entry_point"]
    parts = [f"Goal: {task['goal']}", f"Function name: {entry}",
             "Test cases the implementation must pass:"]
    for t in task["tests"]:
        parts.append(f"- {entry}({t['input']!r}) == {t['expected']!r}")
    if state["candidate"]:
        parts.append("\nYour previous attempt:\n```python\n"
                     + state["candidate"] + "```")
    if state["feedback"]:
        parts.append("\nFEEDBACK on the previous attempt:\n" + state["feedback"])
    return "\n".join(parts)


def drive(run: Run, max_nodes: int | None = None):
    """Execute graph nodes until the run reaches a stopping condition:
    terminal state, waiting on a human, an external pause request, or the
    step budget (used to demonstrate kill-and-resume)."""
    task = run.task
    nodes = task["graph"]["nodes"]
    harness = make_harness(run.meta["harness"])
    executed = 0

    while True:
        state = run.state()

        if state["status"] in ("succeeded", "failed"):
            reason = f" ({state['finish_reason']})" if state["finish_reason"] else ""
            print(f"[{run.run_id}] finished: {state['status']}{reason}")
            if state["promoted_version"]:
                print(f"  promoted version: v{state['promoted_version']}")
            return
        if state["status"] == "waiting_human":
            q = state["hitl"] or {}
            print(f"[{run.run_id}] waiting for human decision at node "
                  f"'{state['node']}'")
            print(f"  question: {q.get('question', '(approve candidate?)')}")
            print(f"  decide with: python -m loopgraph approve {run.run_id}"
                  f"   |   python -m loopgraph reject {run.run_id} --note \"...\"")
            return
        if run.control().get("pause"):
            run.set_control(pause=False)
            run.journal.append("RUN_PAUSED", reason="pause requested")
            print(f"[{run.run_id}] paused (between nodes). "
                  f"Continue with: python -m loopgraph resume {run.run_id}")
            return
        if max_nodes is not None and executed >= max_nodes:
            print(f"[{run.run_id}] step budget reached after {executed} node(s); "
                  f"state is fully journaled.")
            print(f"  continue with: python -m loopgraph resume {run.run_id}")
            return

        node_id = state["node"]
        if node_id not in nodes:
            raise SystemExit(f"graph error: node '{node_id}' is not defined")
        node = nodes[node_id]
        run.journal.append("NODE_STARTED", node=node_id, kind=node["type"])
        executed += 1

        if node["type"] == "agent":
            iteration = state["iteration"] + 1
            print(f"[{run.run_id}] {node_id}: generating candidate "
                  f"(iteration {iteration}, harness={harness.name}) ...")
            raw = harness.complete(SYSTEM_PROMPT, build_prompt(task, state))
            code = extract_code(raw)
            with open(run.candidate_path, "w", encoding="utf-8") as f:
                f.write(code)
            run.journal.append("GENERATED", iteration=iteration, code=code)
            run.journal.append("EDGE_TAKEN", frm=node_id, to=node["next"],
                               label="next")

        elif node["type"] == "verify":
            print(f"[{run.run_id}] {node_id}: running verification ...")
            passed, feedback, results = verifier.run_tests(
                run.task_path, run.candidate_path, task["entry_point"])
            run.journal.append("VERIFIED", passed=passed, feedback=feedback,
                               results=results)
            if passed:
                print(f"  PASS ({len(results)}/{len(results)} tests)")
                run.journal.append("EDGE_TAKEN", frm=node_id,
                                   to=node["on_pass"], label="pass")
            else:
                print("  FAIL:\n    " + (feedback or "").replace("\n", "\n    "))
                max_iters = task.get("max_iterations", 5)
                if state["iteration"] >= max_iters:
                    run.journal.append(
                        "RUN_FINISHED", status="failed",
                        reason=f"still failing after {max_iters} iterations")
                else:
                    run.journal.append("EDGE_TAKEN", frm=node_id,
                                       to=node["on_fail"], label="fail")

        elif node["type"] == "hitl":
            if not task.get("hitl", True):
                run.journal.append("EDGE_TAKEN", frm=node_id,
                                   to=node["on_approve"], label="auto-approve")
            else:
                preview = (state["candidate"] or "")[:800]
                run.journal.append(
                    "HITL_REQUESTED",
                    question=f"Promote iteration {state['iteration']} of "
                             f"'{task['task_id']}' to a new version?",
                    candidate_preview=preview)

        elif node["type"] == "promote":
            store = VersionStore(ROOT, task["task_id"])
            version = store.promote(run.candidate_path, run.run_id)
            run.journal.append("VERSION_PROMOTED", version=version,
                               file=f"v{version}.py")
            run.journal.append("RUN_FINISHED", status="succeeded")
            print(f"[{run.run_id}] promoted candidate as v{version}")

        else:
            raise SystemExit(f"unknown node type: {node['type']}")


def start(task_path: str, harness_name: str | None, max_nodes: int | None):
    name = resolve_harness_name(harness_name)
    run = Run.create(task_path, name)
    print(f"[{run.run_id}] started (task={run.meta['task_id']}, harness={name})")
    drive(run, max_nodes)
    return run.run_id


def resume(run_id: str, max_nodes: int | None):
    run = Run(run_id)
    if not run.journal.read():
        raise SystemExit(f"unknown run: {run_id}")
    state = run.state()
    if state["status"] == "paused":
        run.journal.append("RUN_RESUMED")
    drive(run, max_nodes)


def decide(run_id: str, decision: str, note: str | None):
    run = Run(run_id)
    state = run.state()
    if state["status"] != "waiting_human":
        raise SystemExit(f"run {run_id} is '{state['status']}', not waiting on "
                         f"a human decision")
    node = run.task["graph"]["nodes"][state["node"]]
    run.journal.append("HITL_DECISION", decision=decision, note=note,
                       decided_at_node=state["node"])
    target = node["on_approve"] if decision == "approve" else node["on_reject"]
    run.journal.append("EDGE_TAKEN", frm=state["node"], to=target,
                       label=decision)
    drive(run)
