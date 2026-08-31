"""Harness boundary: a harness is an *agent runtime* (DSH first), not a model
API adapter.

The supervisor's contract with a runtime is process-level:

  1. the supervisor materializes the task brief at <workspace>/instructions.md
  2. it launches the runtime process with the workspace as working directory
  3. the runtime — an autonomous agent session with its own tools, model calls
     and side effects — writes the artifact to <workspace>/candidate.py and
     exits
  4. the supervisor never trusts the runtime's own success claim; the
     out-of-process verifier is the only judge of the artifact

Because a runtime invocation is an opaque external effect, the supervisor
journals intent/result around every invocation, and an interrupted invocation
parks the run as unknown-outcome instead of being retried blindly (see
supervisor.py).

A failed real-harness invocation is never converted into a mock result: the
mock is a separate, explicitly selected harness and every journal entry it
produces is labeled 'mock'.
"""
import os
import shlex
import shutil
import subprocess
import sys
import time


class AgentHarness:
    name = "base"

    def probe(self) -> dict:
        """Identity/version evidence, journaled with every run (the 'pin')."""
        raise NotImplementedError

    def run_task(self, workspace: str) -> dict:
        """Launch the runtime over the workspace; return invocation metadata.
        Must not raise on ordinary failure — the exit code is the signal."""
        raise NotImplementedError


def _split_template(template: str) -> list:
    parts = shlex.split(template, posix=(os.name != "nt"))
    if os.name == "nt":
        parts = [p.strip('"') for p in parts]
    return parts


def _resolve_executable(argv: list) -> list:
    """Resolve argv[0] through PATH (honoring PATHEXT on Windows): npm-style
    launchers install `dsh` as `dsh.cmd`, which the shell finds but a bare
    CreateProcess does not."""
    exe = shutil.which(argv[0])
    return [exe, *argv[1:]] if exe else argv


class CliAgentHarness(AgentHarness):
    """Adapter for any CLI agent runtime. The argv template may reference
    {workspace} and {instructions}; the process runs with cwd=workspace."""

    def __init__(self, name: str, argv_template: list, version_argv: list,
                 timeout: int = 900):
        self.name = name
        self.argv_template = argv_template
        self.version_argv = version_argv
        self.timeout = timeout

    def probe(self) -> dict:
        try:
            proc = subprocess.run(_resolve_executable(self.version_argv),
                                  capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
        except FileNotFoundError:
            raise SystemExit(
                f"harness '{self.name}': runtime executable not found "
                f"({self.version_argv[0]!r}); refusing to continue — a "
                f"missing runtime is never silently replaced by a mock.") \
                from None
        return {
            "harness": self.name,
            "version_command": self.version_argv,
            "version": (proc.stdout or proc.stderr).strip()[:300],
            "exit_code": proc.returncode,
        }

    def _argv(self, workspace: str) -> list:
        instructions = os.path.join(workspace, "instructions.md")
        return _resolve_executable(
            [a.format(workspace=workspace, instructions=instructions)
             for a in self.argv_template])

    def run_task(self, workspace: str) -> dict:
        argv = self._argv(workspace)
        t0 = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=workspace, capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=self.timeout)
            exit_code, out, err = proc.returncode, proc.stdout, proc.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            out = (exc.stdout or b"").decode("utf-8", "replace") \
                if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = f"runtime timed out after {self.timeout}s"
            timed_out = True
        return {
            "harness": self.name,
            "command": argv,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_s": round(time.monotonic() - t0, 2),
            "stdout_tail": (out or "")[-2000:],
            "stderr_tail": (err or "")[-2000:],
        }


class DSHHarness(CliAgentHarness):
    """DSH (DeepSeek Harness): DeepSeek's agent runtime.

    Invocation flags differ across DSH releases, so the exact launch command
    is pinned per deployment instead of guessed:

      LOOPGRAPH_DSH_CMD          e.g. "dsh run --workspace {workspace} --brief {instructions}"
      LOOPGRAPH_DSH_VERSION_CMD  default: "dsh --version"

    The probe output (runtime version) is journaled with every run as the pin
    evidence, so a journal always shows exactly which DSH build produced a
    candidate.
    """
    name = "dsh"

    def __init__(self):
        cmd = os.environ.get("LOOPGRAPH_DSH_CMD")
        if not cmd:
            raise SystemExit(
                "LOOPGRAPH_DSH_CMD is not set. Set it to the exact DSH launch "
                "command (e.g. 'dsh run --workspace {workspace} --brief "
                "{instructions}'). Refusing to guess flags or to substitute "
                "a mock.")
        version_cmd = os.environ.get("LOOPGRAPH_DSH_VERSION_CMD", "dsh --version")
        super().__init__("dsh", _split_template(cmd),
                         _split_template(version_cmd),
                         timeout=int(os.environ.get("LOOPGRAPH_DSH_TIMEOUT", "900")))


class MockHarness(CliAgentHarness):
    """Scripted stand-in runtime (loopgraph/mock_runtime.py) launched through
    the exact same subprocess seam as a real runtime. Exists for offline,
    deterministic exercising of the outer loop; always explicitly selected
    and always labeled 'mock'."""
    name = "mock"

    def __init__(self):
        # Invoked by absolute script path: the runtime runs with
        # cwd=<workspace>, where the loopgraph package is not importable —
        # exactly like any external runtime binary would be launched.
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "mock_runtime.py")
        super().__init__(
            "mock",
            [sys.executable, script, "{workspace}"],
            [sys.executable, script, "--version"],
            timeout=120,
        )


HARNESSES = {
    "dsh": DSHHarness,
    "mock": MockHarness,
}


def resolve_harness_name(name: str | None) -> str:
    """DSH-first policy: an explicit choice wins; otherwise DSH is selected
    when a pinned launch command is configured; otherwise the mock is used
    with a visible notice (never a silent substitution)."""
    if name:
        if name not in HARNESSES:
            raise SystemExit(
                f"unknown harness '{name}'; available: {', '.join(sorted(HARNESSES))}")
        return name
    if os.environ.get("LOOPGRAPH_DSH_CMD"):
        return "dsh"
    hint = ""
    if shutil.which("dsh"):
        hint = (" A 'dsh' executable is on PATH — set LOOPGRAPH_DSH_CMD to "
                "use it.")
    print(f"[harness] LOOPGRAPH_DSH_CMD not configured; using the offline "
          f"mock runtime.{hint}")
    return "mock"


def make_harness(name: str) -> AgentHarness:
    return HARNESSES[name]()
