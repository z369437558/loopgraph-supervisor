# Real DSH run evidence — 2026-08-31, run `20260831-214851-90ec60`

Files copied verbatim from the run's control plane and workspace after a real
end-to-end run against **DSH 0.1.1-rc.2** (`@deepseek-ai/dsh`, installed from
npm, exact version pinned):

- `journal.jsonl` — the full event journal. Note in particular:
  - `HARNESS_PROBED` (seq 2): runtime identity probe, `dsh --version` →
    `0.1.1-rc.2`, journaled before the first node ran.
  - `EFFECT_INTENT` / `EFFECT_RESULT` (seq 4/5): the exact command line, exit
    code, wall-clock duration (11.2s) and the runtime's stdout tail for the
    single DSH invocation.
  - `HOLDOUT_VERIFIED` (seq 12): the DSH-written candidate passed all three
    hidden holdout cases on iteration 1 — cases that never appeared in the
    brief it was given (see `instructions.md` in this directory: it contains
    only the three visible tests).
  - `HITL_DECISION` (seq 14): approval bound to `request_id`,
    `candidate_hash` and `spec_hash`, with the acting OS user.
- `instructions.md` — the exact brief the runtime received (visible tests
  only; no holdout values).
- `candidate.py` — the artifact DSH wrote into its workspace.
- `meta.json` — harness name and the frozen spec revision for the run.

Command configuration used (API key passed via environment, never on the
command line or in any file):

```
LOOPGRAPH_DSH_CMD='dsh --profile headless "Follow the brief at {instructions} exactly. Write the complete implementation to candidate.py in the current working directory."'
```

Reproduce with your own `DEEPSEEK_API_KEY`:

```bash
export LOOPGRAPH_DSH_CMD='dsh --profile headless "Follow the brief at {instructions} exactly. Write the complete implementation to candidate.py in the current working directory."'
python -m loopgraph run demo/slugify.json
```
