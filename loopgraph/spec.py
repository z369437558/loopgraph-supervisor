"""First-class LoopSpec: the graph is an immutable, content-addressed,
versionable object — not code inside the engine.

A spec revision is identified by the SHA-256 of its canonical JSON form.
The registry never overwrites: registering the same content is a no-op,
registering different content yields a new revision. Every run freezes the
exact revision it executes and the supervisor refuses to drive a run whose
frozen spec no longer matches its recorded hash.
"""
import hashlib
import json
import os

VALID_NODE_TYPES = {"agent", "verify", "hitl", "promote"}
REQUIRED_KEYS = {"task_id", "goal", "entry_point", "tests", "graph"}
EDGE_KEYS = {"next", "on_pass", "on_fail", "on_approve", "on_reject"}


def canonical(spec: dict) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical(spec).encode("utf-8")).hexdigest()


def validate(spec: dict):
    missing = REQUIRED_KEYS - set(spec)
    if missing:
        raise SystemExit(f"spec invalid: missing keys {sorted(missing)}")
    graph = spec["graph"]
    nodes = graph.get("nodes") or {}
    entry = graph.get("entry")
    if entry not in nodes:
        raise SystemExit(f"spec invalid: entry node '{entry}' not in graph")
    for nid, node in nodes.items():
        if node.get("type") not in VALID_NODE_TYPES:
            raise SystemExit(
                f"spec invalid: node '{nid}' has unknown type "
                f"'{node.get('type')}'")
        for key, target in node.items():
            if key in EDGE_KEYS and target not in nodes:
                raise SystemExit(
                    f"spec invalid: edge {nid}.{key} -> '{target}' "
                    f"points at an undefined node")
    if not spec["tests"]:
        raise SystemExit("spec invalid: at least one visible test is required")


class SpecStore:
    """Content-addressed, append-only registry of LoopSpec revisions."""

    def __init__(self, root: str):
        self.dir = os.path.join(root, "specs")

    def _task_dir(self, task_id: str) -> str:
        return os.path.join(self.dir, task_id)

    def register(self, spec: dict) -> str:
        validate(spec)
        h = spec_hash(spec)
        d = self._task_dir(spec["task_id"])
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{h}.json")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(canonical(spec))
        return h

    def load(self, task_id: str, h: str) -> dict:
        path = os.path.join(self._task_dir(task_id), f"{h}.json")
        if not os.path.exists(path):
            raise SystemExit(f"unknown spec revision {h} for task '{task_id}'")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def revisions(self, task_id: str) -> list:
        d = self._task_dir(task_id)
        if not os.path.isdir(d):
            return []
        out = []
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                out.append({"hash": name[:-5],
                            "mtime": os.path.getmtime(os.path.join(d, name))})
        return out
