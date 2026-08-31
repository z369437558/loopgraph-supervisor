"""Verification: run the candidate artifact against the task's test cases in a
separate interpreter process (isolation + timeout). Failures come back as
structured feedback that is fed into the next generation prompt.
"""
import json
import subprocess
import sys

_RUNNER = r"""
import json, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
ns = {}
exec(open(sys.argv[2], encoding="utf-8").read(), ns)
fn = ns[spec["entry_point"]]
results = []
for case in spec[sys.argv[3]]:
    try:
        got = fn(case["input"])
        results.append({"input": case["input"], "expected": case["expected"],
                        "got": got, "passed": got == case["expected"]})
    except Exception as exc:
        results.append({"input": case["input"], "expected": case["expected"],
                        "got": "raised %s: %s" % (type(exc).__name__, exc),
                        "passed": False})
print(json.dumps(results))
"""


def run_tests(task_path: str, candidate_path: str, entry_point: str,
              suite: str = "tests", timeout: int = 20):
    """Run one test suite of the spec ('tests' or 'holdout_tests') against
    the candidate. Returns (passed, feedback, results)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER, task_path, candidate_path, suite],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"candidate timed out after {timeout}s", []
    if proc.returncode != 0:
        tail = proc.stderr.strip()[-2000:]
        return False, f"candidate failed to load or crashed the runner:\n{tail}", []
    results = json.loads(proc.stdout)
    failed = [r for r in results if not r["passed"]]
    if not failed:
        return True, None, results
    lines = [
        f"- {entry_point}({r['input']!r}) returned {r['got']!r}, "
        f"expected {r['expected']!r}"
        for r in failed
    ]
    feedback = (f"{len(failed)}/{len(results)} tests failed:\n" + "\n".join(lines))
    return False, feedback, results
