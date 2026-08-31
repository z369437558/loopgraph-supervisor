"""Scripted stand-in agent runtime (see MockHarness in harness.py).

Behaves like a minimal agent session: reads the task brief from
<workspace>/instructions.md, writes its artifact to <workspace>/candidate.py,
and exits. Deliberately imperfect on the first pass — it only produces a
correct implementation once the brief carries feedback — so the outer loop's
fail -> feedback -> retry path is genuinely exercised offline.
"""
import os
import sys

VERSION = "loopgraph-mock-runtime 1.0.0 (deterministic)"

NAIVE = 'def slugify(text):\n    return text.lower().replace(" ", "-")\n'
FIXED = (
    "import re\n\n"
    "def slugify(text):\n"
    '    text = re.sub(r"[^a-z0-9]+", "-", text.lower())\n'
    '    return text.strip("-")\n'
)


def main() -> int:
    if "--version" in sys.argv:
        print(VERSION)
        return 0
    workspace = sys.argv[1]
    with open(os.path.join(workspace, "instructions.md"), encoding="utf-8") as f:
        brief = f.read()
    code = FIXED if "FEEDBACK" in brief else NAIVE
    with open(os.path.join(workspace, "candidate.py"), "w", encoding="utf-8") as f:
        f.write(code)
    print("mock runtime: wrote candidate.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
