"""Version store: promoted artifacts are immutable, append-only versions with a
manifest `current` pointer, so promotion and rollback are both O(1) pointer
moves and the full promote/rollback history stays auditable.
"""
import json
import os
import shutil
import time


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class VersionStore:
    def __init__(self, root: str, task_id: str):
        self.dir = os.path.join(root, "artifacts", task_id)
        os.makedirs(self.dir, exist_ok=True)
        self.manifest_path = os.path.join(self.dir, "manifest.json")

    def manifest(self) -> dict:
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path, encoding="utf-8") as f:
                return json.load(f)
        return {"current": None, "versions": [], "log": []}

    def _save(self, m: dict):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)

    def promote(self, candidate_path: str, run_id: str) -> int:
        m = self.manifest()
        # Crash-recovery idempotency: a run promotes at most once, so if this
        # run already has a version (crash after copy, before the journal
        # event), reuse it instead of promoting a duplicate.
        for v in m["versions"]:
            if v["run_id"] == run_id:
                return v["version"]
        n = len(m["versions"]) + 1
        dst = os.path.join(self.dir, f"v{n}.py")
        shutil.copyfile(candidate_path, dst)
        m["versions"].append({"version": n, "run_id": run_id, "ts": _now(),
                              "file": os.path.basename(dst)})
        m["log"].append({"ts": _now(), "action": "promote",
                         "from": m["current"], "to": n, "run_id": run_id})
        m["current"] = n
        self._save(m)
        return n

    def rollback(self, to: int | None = None) -> int:
        m = self.manifest()
        if not m["versions"]:
            raise SystemExit("no promoted versions to roll back")
        if to is None:
            if m["current"] is None or m["current"] <= 1:
                raise SystemExit("no earlier version to roll back to")
            to = m["current"] - 1
        if not any(v["version"] == to for v in m["versions"]):
            raise SystemExit(f"version v{to} does not exist")
        m["log"].append({"ts": _now(), "action": "rollback",
                         "from": m["current"], "to": to})
        m["current"] = to
        self._save(m)
        return to

    def current_file(self) -> str | None:
        m = self.manifest()
        if m["current"] is None:
            return None
        return os.path.join(self.dir, f"v{m['current']}.py")
