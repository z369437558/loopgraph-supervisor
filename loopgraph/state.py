"""Run state as a pure fold over journal events (event sourcing).

Crash recovery, `resume`, `status`, and HITL routing all reuse this single
reducer, so there is exactly one definition of "where is this run right now".
"""
import copy

INITIAL: dict = {
    "status": "created",      # created|running|paused|waiting_human|succeeded|failed
    "spec_hash": None,        # LoopSpec revision this run is bound to
    "node": None,             # node id the run is currently at
    "iteration": 0,
    "candidate": None,        # latest generated artifact (source text)
    "feedback": None,         # verifier/human feedback for the next generation
    "verified": False,
    "holdout": None,          # latest hidden-holdout result, if the spec has one
    "promoted_version": None,
    "hitl": None,             # pending HITL request payload, if any
    "last_approval": None,    # approval bound to a specific candidate hash
    "pending_effect": None,   # journaled effect intent with no recorded outcome
    "finish_reason": None,
}


def replay(events: list) -> dict:
    s = copy.deepcopy(INITIAL)
    for e in events:
        t, p = e["type"], e["payload"]
        if t == "RUN_STARTED":
            s["status"] = "running"
            s["node"] = p["entry_node"]
            s["spec_hash"] = p.get("spec_hash")
        elif t == "NODE_STARTED":
            s["node"] = p["node"]
        elif t == "GENERATED":
            s["iteration"] = p["iteration"]
            s["candidate"] = p["code"]
        elif t == "VERIFIED":
            s["verified"] = p["passed"]
            s["feedback"] = p["feedback"]
        elif t == "HOLDOUT_VERIFIED":
            s["holdout"] = {"passed": p["passed"]}
            if not p["passed"]:
                # The agent only ever sees the generic message — holdout
                # inputs/expectations must not leak into the loop.
                s["verified"] = False
                s["feedback"] = p.get("agent_feedback") or \
                    "The hidden holdout evaluation failed."
        elif t == "EDGE_TAKEN":
            s["node"] = p["to"]
        elif t == "EFFECT_INTENT":
            s["pending_effect"] = p
        elif t in ("EFFECT_RESULT", "EFFECT_RESOLVED"):
            s["pending_effect"] = None
        elif t == "HITL_REQUESTED":
            s["status"] = "waiting_human"
            s["hitl"] = p
        elif t == "HITL_DECISION":
            s["status"] = "running"
            s["hitl"] = None
            if p["decision"] == "reject":
                s["verified"] = False
                s["feedback"] = p.get("note") or "Rejected by human reviewer."
            else:
                s["last_approval"] = {
                    "request_id": p.get("request_id"),
                    "candidate_hash": p.get("candidate_hash"),
                    "actor": p.get("actor"),
                }
        elif t == "RUN_PAUSED":
            s["status"] = "paused"
        elif t == "RUN_RESUMED":
            s["status"] = "running"
        elif t == "VERSION_PROMOTED":
            s["promoted_version"] = p["version"]
        elif t == "RUN_FINISHED":
            s["status"] = p["status"]
            s["finish_reason"] = p.get("reason")
    # An intent with no recorded outcome means the process died mid-effect:
    # the effect may or may not have happened. That is an unknown outcome and
    # must never be retried automatically (single-writer per run is assumed).
    if s["pending_effect"] is not None and s["status"] == "running":
        s["status"] = "unknown_outcome"
    return s
