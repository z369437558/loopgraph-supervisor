"""Append-only event journal.

The journal is the single source of truth for a run: state is always derived
by replaying events (see state.py), never stored as a mutable object. This is
what makes every run pausable, crash-recoverable, and auditable for free.
"""
import json
import os
import time


class Journal:
    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self.path = os.path.join(run_dir, "journal.jsonl")
        os.makedirs(run_dir, exist_ok=True)

    def append(self, event_type: str, **payload) -> dict:
        event = {
            "seq": len(self.read()) + 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": event_type,
            "payload": payload,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read(self) -> list:
        if not os.path.exists(self.path):
            return []
        events = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
