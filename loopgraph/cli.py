"""CLI: every capability of the supervisor is observable and drivable from
here — run/resume/pause, HITL decisions, event history, versions, rollback."""
import argparse
import json
import os

from . import supervisor
from .spec import SpecStore
from .supervisor import ROOT, RUNS_DIR, Run
from .versions import VersionStore


def _print_status(run: Run):
    state = run.state()
    task = run.task
    graph = task["graph"]["nodes"]
    print(f"run:       {run.run_id}")
    print(f"task:      {task['task_id']}  (harness={run.meta['harness']})")
    if state["spec_hash"]:
        print(f"spec:      {state['spec_hash'][:12]} (immutable revision)")
    print(f"status:    {state['status']}"
          + (f"  ({state['finish_reason']})" if state["finish_reason"] else ""))
    print(f"iteration: {state['iteration']}/{task.get('max_iterations', 5)}")
    print(f"verified:  {state['verified']}")
    if state["pending_effect"]:
        eff = state["pending_effect"]
        print(f"pending:   effect {eff['effect_id']} ({eff['kind']}) has no "
              f"recorded outcome — resolve before the run can continue")
    if state["promoted_version"]:
        print(f"promoted:  v{state['promoted_version']}")
    print("graph:")
    for nid, node in graph.items():
        marker = " <== current" if nid == state["node"] else ""
        edges = {k: v for k, v in node.items() if k != "type"}
        edge_s = ", ".join(f"{k.replace('on_', '')}->{v}" for k, v in edges.items())
        print(f"  [{node['type']:>7}] {nid:<10} {edge_s}{marker}")
    print(f"events:    {len(run.journal.read())} journaled "
          f"(see: python -m loopgraph history {run.run_id})")


def _print_history(run: Run):
    for e in run.journal.read():
        p = dict(e["payload"])
        for key in ("code", "candidate_preview"):
            if key in p:
                p[key] = f"<{len(p[key])} chars>"
        if "results" in p:
            p["results"] = f"<{len(p['results'])} test results>"
        if p.get("feedback"):
            p["feedback"] = p["feedback"].split("\n")[0] + " ..."
        detail = ", ".join(f"{k}={v}" for k, v in p.items())
        print(f"{e['seq']:>3}  {e['ts']}  {e['type']:<17} {detail}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="loopgraph",
        description="DSH-first, harness-neutral, durably-recoverable "
                    "LoopGraph supervisor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="start a new run from a task spec")
    p.add_argument("task")
    p.add_argument("--harness", help="deepseek | mock (default: deepseek if "
                                     "DEEPSEEK_API_KEY is set, else mock)")
    p.add_argument("--step", type=int, metavar="N",
                   help="execute at most N nodes then exit (demonstrates "
                        "durable stop/resume)")

    p = sub.add_parser("resume", help="resume a paused/interrupted run")
    p.add_argument("run_id")
    p.add_argument("--step", type=int, metavar="N")

    p = sub.add_parser("pause", help="request a pause (applied between nodes)")
    p.add_argument("run_id")

    p = sub.add_parser("approve", help="HITL: approve the pending candidate")
    p.add_argument("run_id")
    p.add_argument("--note")

    p = sub.add_parser("reject", help="HITL: reject; note becomes feedback for "
                                      "the next iteration")
    p.add_argument("run_id")
    p.add_argument("--note")

    p = sub.add_parser("resolve-effect",
                       help="resolve an unknown-outcome effect after "
                            "inspecting the workspace")
    p.add_argument("run_id")
    p.add_argument("--outcome", required=True,
                   choices=["not-executed", "completed"])
    p.add_argument("--note")

    p = sub.add_parser("status", help="show run state and graph position")
    p.add_argument("run_id")

    p = sub.add_parser("history", help="show the run's full event journal")
    p.add_argument("run_id")

    sub.add_parser("runs", help="list all runs")

    p = sub.add_parser("versions", help="list promoted versions of a task")
    p.add_argument("task_id")

    p = sub.add_parser("rollback", help="move a task's current version pointer "
                                        "back")
    p.add_argument("task_id")
    p.add_argument("--to", type=int, help="target version (default: current-1)")

    p = sub.add_parser("show", help="print the current promoted artifact")
    p.add_argument("task_id")

    p = sub.add_parser("spec", help="list registered LoopSpec revisions of a "
                                    "task, or print one")
    p.add_argument("task_id")
    p.add_argument("--show", metavar="HASH", help="print this revision")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        supervisor.start(args.task, args.harness, args.step)
    elif args.cmd == "resume":
        supervisor.resume(args.run_id, args.step)
    elif args.cmd == "pause":
        run = Run(args.run_id)
        run.set_control(pause=True)
        print(f"pause requested for {args.run_id}; it takes effect at the next "
              f"node boundary of a live driver, and is honored by `resume`.")
    elif args.cmd == "resolve-effect":
        supervisor.resolve_effect(args.run_id, args.outcome, args.note)
    elif args.cmd == "approve":
        supervisor.decide(args.run_id, "approve", args.note)
    elif args.cmd == "reject":
        supervisor.decide(args.run_id, "reject", args.note)
    elif args.cmd == "status":
        _print_status(Run(args.run_id))
    elif args.cmd == "history":
        _print_history(Run(args.run_id))
    elif args.cmd == "runs":
        if not os.path.isdir(RUNS_DIR):
            print("(no runs yet)")
            return
        for rid in sorted(os.listdir(RUNS_DIR)):
            run = Run(rid)
            s = run.state()
            print(f"{rid}  {s['status']:<14} node={s['node']} "
                  f"iter={s['iteration']}")
    elif args.cmd == "versions":
        m = VersionStore(ROOT, args.task_id).manifest()
        if not m["versions"]:
            print("(no promoted versions)")
            return
        for v in m["versions"]:
            cur = "  <== current" if v["version"] == m["current"] else ""
            print(f"v{v['version']}  {v['ts']}  from run {v['run_id']}{cur}")
        print("log:")
        for entry in m["log"]:
            print(f"  {entry['ts']}  {entry['action']}: "
                  f"{entry.get('from')} -> {entry.get('to')}")
    elif args.cmd == "rollback":
        to = VersionStore(ROOT, args.task_id).rollback(args.to)
        print(f"current version of '{args.task_id}' is now v{to}")
    elif args.cmd == "spec":
        store = SpecStore(ROOT)
        if args.show:
            print(json.dumps(store.load(args.task_id, args.show), indent=2))
        else:
            revs = store.revisions(args.task_id)
            if not revs:
                print("(no registered revisions)")
            for r in revs:
                print(r["hash"])
    elif args.cmd == "show":
        f = VersionStore(ROOT, args.task_id).current_file()
        if f is None:
            print("(no promoted versions)")
        else:
            print(f"# {f}")
            with open(f, encoding="utf-8") as fh:
                print(fh.read())


if __name__ == "__main__":
    main()
