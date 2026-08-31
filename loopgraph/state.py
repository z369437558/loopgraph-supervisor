"""Run state as a pure fold over journal events (event sourcing).

Crash recovery, `resume`, `status`, and HITL routing all reuse this single
reducer, so there is exactly one definition of "where is this run right now".
"""
import copy

INITIAL = {
    "status": "created",      # created|running|paused|waiting_human|succeeded|failed
    "node": None,             # node id the run is currently at
    "iteration": 0,
    "candidate": None,        # latest generated artifact (source text)
    "feedback": None,         # verifier/human feedback for the next generation
    "verified": False,
    "promoted_version": None,
    "hitl": None,             # pending HITL request payload, if any
    "finish_reason": None,
}


def replay(events: list) -> dict:
    s = copy.deepcopy(INITIAL)
    for e in events:
        t, p = e["type"], e["payload"]
        if t == "RUN_STARTED":
            s["status"] = "running"
            s["node"] = p["entry_node"]
        elif t == "NODE_STARTED":
            s["node"] = p["node"]
        elif t == "GENERATED":
            s["iteration"] = p["iteration"]
            s["candidate"] = p["code"]
        elif t == "VERIFIED":
            s["verified"] = p["passed"]
            s["feedback"] = p["feedback"]
        elif t == "EDGE_TAKEN":
            s["node"] = p["to"]
        elif t == "HITL_REQUESTED":
            s["status"] = "waiting_human"
            s["hitl"] = p
        elif t == "HITL_DECISION":
            s["status"] = "running"
            s["hitl"] = None
            if p["decision"] == "reject":
                s["verified"] = False
                s["feedback"] = p.get("note") or "Rejected by human reviewer."
        elif t == "RUN_PAUSED":
            s["status"] = "paused"
        elif t == "RUN_RESUMED":
            s["status"] = "running"
        elif t == "VERSION_PROMOTED":
            s["promoted_version"] = p["version"]
        elif t == "RUN_FINISHED":
            s["status"] = p["status"]
            s["finish_reason"] = p.get("reason")
    return s
