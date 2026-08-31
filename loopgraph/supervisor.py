"""LoopGraph supervisor: interprets a task's LoopSpec graph, journaling every
step. The drive loop never holds state that is not already in the journal, so
the process can be killed between any two node executions and `resume`
continues exactly where it stopped.

The harness is an agent runtime driven through a process-level contract (see
harness.py): the supervisor writes the task brief into the run's workspace,
launches the runtime, and afterwards picks up <workspace>/candidate.py. The
runtime's own opinion of its success is never used — verification is external.
"""
import getpass
import hashlib
import json
import os
import time
import uuid

from . import verify as verifier
from .harness import make_harness, resolve_harness_name
from .journal import Journal
from .spec import SpecStore, canonical, spec_hash
from .state import replay
from .versions import VersionStore

ROOT = os.environ.get(
    "LOOPGRAPH_HOME",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
RUNS_DIR = os.path.join(ROOT, "runs")

# When the hidden holdout fails, the agent gets ONLY this message — holdout
# inputs and expectations never enter the loop, otherwise "generalization"
# would just be a second visible test set.
GENERIC_HOLDOUT_FEEDBACK = (
    "The candidate passed the visible tests but failed a hidden holdout "
    "evaluation. Do not overfit to the listed examples; implement the stated "
    "goal in full generality.")


def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


class Run:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = os.path.join(RUNS_DIR, run_id)
        self.journal = Journal(self.dir)

    @classmethod
    def create(cls, task_path: str, harness_name: str) -> "Run":
        with open(task_path, encoding="utf-8") as f:
            task = json.load(f)
        # Register the spec revision (content-addressed, immutable) before
        # anything else: the run is bound to that exact revision.
        h = SpecStore(ROOT).register(task)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run = cls(run_id)
        os.makedirs(run.workspace, exist_ok=True)
        # The run directory is self-contained: frozen spec (canonical form,
        # so its hash can be re-verified), meta, journal, workspace.
        with open(run.task_path, "w", encoding="utf-8") as f:
            f.write(canonical(task))
        with open(os.path.join(run.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"harness": harness_name, "task_id": task["task_id"],
                       "spec_hash": h}, f)
        # Probe the runtime before journaling the start: the probe output is
        # the per-run pin evidence of exactly which runtime build is in use.
        probe = make_harness(harness_name).probe()
        run.journal.append("RUN_STARTED", task_id=task["task_id"],
                           entry_node=task["graph"]["entry"],
                           harness=harness_name, spec_hash=h)
        run.journal.append("HARNESS_PROBED", **probe)
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
    def workspace(self) -> str:
        return os.path.join(self.dir, "workspace")

    @property
    def candidate_path(self) -> str:
        return os.path.join(self.workspace, "candidate.py")

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


def build_instructions(task: dict, state: dict) -> str:
    """The task brief handed to the agent runtime. Contains only the visible
    tests — holdout cases must never appear here."""
    entry = task["entry_point"]
    lines = [
        "# Task brief",
        f"Goal: {task['goal']}",
        f"Function name: `{entry}`",
        "",
        "Visible test cases the implementation must pass:",
    ]
    for t in task["tests"]:
        lines.append(f"- `{entry}({t['input']!r}) == {t['expected']!r}`")
    lines += [
        "",
        "## Output contract",
        "Write the complete, self-contained implementation to `candidate.py`",
        "in this directory, then exit. Do not claim success yourself —",
        "an external verifier is the only judge of the artifact.",
    ]
    if state["candidate"]:
        lines += ["", "## Previous attempt", "```python",
                  state["candidate"].rstrip(), "```"]
    if state["feedback"]:
        lines += ["", "## FEEDBACK on the previous attempt", state["feedback"]]
    return "\n".join(lines) + "\n"


def drive(run: Run, max_nodes: int | None = None):
    """Execute graph nodes until a stopping condition: terminal state, waiting
    on a human, an external pause request, or the step budget (used to
    demonstrate kill-and-resume)."""
    task = run.task
    expected = run.meta.get("spec_hash")
    if expected and spec_hash(task) != expected:
        raise SystemExit(
            f"refusing to drive {run.run_id}: its frozen spec no longer "
            f"matches the registered revision {expected[:12]} — the spec is "
            f"immutable; start a new run for a new revision.")
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
        if state["status"] == "unknown_outcome":
            eff = state["pending_effect"] or {}
            print(f"[{run.run_id}] parked: effect {eff.get('effect_id')} "
                  f"({eff.get('kind')} via {eff.get('harness')}) was started "
                  f"but has no recorded outcome — NOT retrying automatically.")
            print(f"  inspect {run.workspace} (candidate.py content/mtime), "
                  f"then resolve with one of:")
            print(f"    python -m loopgraph resolve-effect {run.run_id} "
                  f"--outcome not-executed   (safe to re-run the runtime)")
            print(f"    python -m loopgraph resolve-effect {run.run_id} "
                  f"--outcome completed      (recover candidate.py as the result)")
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
            with open(os.path.join(run.workspace, "instructions.md"), "w",
                      encoding="utf-8") as f:
                f.write(build_instructions(task, state))
            # A runtime invocation is an external effect with arbitrary side
            # effects: journal the intent BEFORE launching, so a crash while
            # the runtime is (possibly) working leaves an unambiguous
            # unknown-outcome marker instead of an invisible gap.
            effect_id = uuid.uuid4().hex[:12]
            run.journal.append("EFFECT_INTENT", effect_id=effect_id,
                               kind="harness.run_task", harness=harness.name,
                               node=node_id, iteration=iteration)
            if os.environ.get("LOOPGRAPH_CRASH_AFTER_INTENT"):
                print(f"[{run.run_id}] simulated crash after effect intent "
                      f"{effect_id} (LOOPGRAPH_CRASH_AFTER_INTENT)")
                raise SystemExit(70)
            print(f"[{run.run_id}] {node_id}: launching {harness.name} runtime "
                  f"over workspace (iteration {iteration}) ...")
            meta = harness.run_task(run.workspace)
            produced = os.path.exists(run.candidate_path)
            ok = meta["exit_code"] == 0 and produced
            run.journal.append("EFFECT_RESULT", effect_id=effect_id, ok=ok,
                               iteration=iteration, meta=meta)
            if not ok:
                why = ("runtime exited "
                       f"{meta['exit_code']}" if meta["exit_code"] != 0
                       else "runtime produced no candidate.py")
                run.journal.append("RUN_FINISHED", status="failed",
                                   reason=f"harness invocation failed: {why}")
                continue
            with open(run.candidate_path, encoding="utf-8") as f:
                code = f.read()
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
            # The hidden holdout gate runs first — with or without a human in
            # the loop, a candidate that only memorized the visible tests
            # must not reach promotion.
            if task.get("holdout_tests"):
                h_passed, h_detail, h_results = verifier.run_tests(
                    run.task_path, run.candidate_path, task["entry_point"],
                    suite="holdout_tests")
                run.journal.append(
                    "HOLDOUT_VERIFIED", passed=h_passed, results=h_results,
                    detail=h_detail,
                    agent_feedback=None if h_passed else GENERIC_HOLDOUT_FEEDBACK)
                if not h_passed:
                    n_fail = sum(1 for r in h_results if not r["passed"])
                    print(f"  holdout FAIL ({n_fail}/{len(h_results)} hidden "
                          f"cases); details stay out of the agent loop")
                    max_iters = task.get("max_iterations", 5)
                    if state["iteration"] >= max_iters:
                        run.journal.append(
                            "RUN_FINISHED", status="failed",
                            reason=f"holdout still failing after "
                                   f"{max_iters} iterations")
                    else:
                        run.journal.append("EDGE_TAKEN", frm=node_id,
                                           to=node["on_reject"],
                                           label="holdout-fail")
                    continue
                print(f"  holdout PASS ({len(h_results)} hidden cases)")
            if not task.get("hitl", True):
                run.journal.append("EDGE_TAKEN", frm=node_id,
                                   to=node["on_approve"], label="auto-approve")
            else:
                # The approval request is bound to this exact candidate and
                # spec revision; a decision only applies if the binding still
                # holds when it is made (see decide()).
                run.journal.append(
                    "HITL_REQUESTED",
                    request_id=uuid.uuid4().hex[:12],
                    candidate_hash=file_hash(run.candidate_path),
                    spec_hash=run.meta.get("spec_hash"),
                    iteration=state["iteration"],
                    question=f"Promote iteration {state['iteration']} of "
                             f"'{task['task_id']}' to a new version?",
                    candidate_preview=(state["candidate"] or "")[:800])

        elif node["type"] == "promote":
            # Defense in depth: promotion re-checks its preconditions instead
            # of trusting that only legal paths reach this node.
            if task.get("holdout_tests") and not (
                    state["holdout"] and state["holdout"]["passed"]):
                run.journal.append("RUN_FINISHED", status="failed",
                                   reason="promotion blocked: holdout is not "
                                          "green for the current candidate")
                continue
            if task.get("hitl", True):
                approval = state["last_approval"]
                current = file_hash(run.candidate_path)
                if not approval or approval["candidate_hash"] != current:
                    run.journal.append(
                        "RUN_FINISHED", status="failed",
                        reason="promotion blocked: no approval is bound to "
                               "the current candidate")
                    continue
            store = VersionStore(ROOT, task["task_id"])
            version = store.promote(run.candidate_path, run.run_id,
                                    candidate_hash=file_hash(run.candidate_path),
                                    spec_hash=run.meta.get("spec_hash"))
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


def resolve_effect(run_id: str, outcome: str, note: str | None):
    """Human resolution of an unknown-outcome effect. 'not-executed' declares
    the runtime never did its work (safe to re-run); 'completed' declares the
    runtime finished and its artifact is recovered from the workspace. The
    caller is expected to have inspected the workspace first — that judgment
    is exactly what must not be automated."""
    run = Run(run_id)
    state = run.state()
    if state["status"] != "unknown_outcome":
        raise SystemExit(f"run {run_id} has no unresolved effect "
                         f"(status: {state['status']})")
    eff = state["pending_effect"]
    actor = getpass.getuser()
    if outcome == "completed":
        if not os.path.exists(run.candidate_path):
            raise SystemExit("cannot resolve as completed: the workspace has "
                             "no candidate.py to recover")
        with open(run.candidate_path, encoding="utf-8") as f:
            code = f.read()
        run.journal.append("EFFECT_RESOLVED", effect_id=eff["effect_id"],
                           outcome=outcome, actor=actor, note=note)
        run.journal.append("GENERATED", iteration=eff["iteration"], code=code,
                           recovered="from workspace after human confirmation")
        node = run.task["graph"]["nodes"][eff["node"]]
        run.journal.append("EDGE_TAKEN", frm=eff["node"], to=node["next"],
                           label="next")
    else:
        run.journal.append("EFFECT_RESOLVED", effect_id=eff["effect_id"],
                           outcome=outcome, actor=actor, note=note)
    drive(run)


def decide(run_id: str, decision: str, note: str | None):
    run = Run(run_id)
    state = run.state()
    if state["status"] != "waiting_human":
        raise SystemExit(f"run {run_id} is '{state['status']}', not waiting on "
                         f"a human decision")
    request = state["hitl"]
    # An approval is only valid for the exact candidate it was requested for:
    # if the artifact changed since the request, the decision must not carry
    # over to content the human never looked at.
    current = file_hash(run.candidate_path)
    if current != request["candidate_hash"]:
        raise SystemExit(
            f"refusing {decision}: the candidate changed since this approval "
            f"was requested (requested for {request['candidate_hash']}, "
            f"workspace now has {current}). Re-verify before deciding.")
    node = run.task["graph"]["nodes"][state["node"]]
    run.journal.append("HITL_DECISION", decision=decision, note=note,
                       decided_at_node=state["node"],
                       request_id=request["request_id"],
                       candidate_hash=current,
                       spec_hash=request.get("spec_hash"),
                       actor=getpass.getuser())
    target = node["on_approve"] if decision == "approve" else node["on_reject"]
    run.journal.append("EDGE_TAKEN", frm=state["node"], to=target,
                       label=decision)
    drive(run)
