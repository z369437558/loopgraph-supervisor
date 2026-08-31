import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(REPO, "demo")


@pytest.fixture()
def home(tmp_path):
    """Isolated LOOPGRAPH_HOME per test: runs/, specs/, artifacts/ land here."""
    return str(tmp_path)


def cli(home, *args, env=None, check=True):
    e = dict(os.environ, LOOPGRAPH_HOME=home, PYTHONIOENCODING="utf-8")
    e.pop("LOOPGRAPH_CRASH_AFTER_INTENT", None)
    e.pop("LOOPGRAPH_DSH_CMD", None)
    if env:
        e.update(env)
    proc = subprocess.run([sys.executable, "-m", "loopgraph", *args],
                          capture_output=True, text=True, cwd=REPO, env=e,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError(
            f"cli {args} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def last_run_id(home):
    return sorted(os.listdir(os.path.join(home, "runs")))[-1]


def run_dir(home, run_id):
    return os.path.join(home, "runs", run_id)


def workspace_dir(home, run_id):
    return os.path.join(home, "workspaces", run_id)
