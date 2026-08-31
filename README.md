# LoopGraph Supervisor

A **DSH-first, harness-neutral, durably-recoverable** supervisor for agent
improvement loops: an agent runtime produces an artifact, an independent
verifier tests it, failures feed back into the next iteration, a hidden
holdout guards against overfitting, a human approval — bound to the exact
candidate — gates promotion, and every promoted version is immutable and
rollbackable.

Pure Python stdlib at runtime. No API keys or network needed to run the full
demo: the mock runtime exercises every path offline and deterministically.

## The three objects

1. **LoopSpec** — the graph is data, and it is *first-class*: a spec revision
   is the SHA-256 of its canonical JSON, registered in an append-only,
   content-addressed `SpecStore`. Runs freeze the exact revision they
   execute; the driver refuses to run if the frozen spec no longer matches
   its registered hash. Formatting edits don't change a revision; semantic
   edits do, and require a new run.
2. **Run** — an append-only event journal (`journal.jsonl`). Run state is a
   pure fold over events (`state.py`); recovery, resume, status, and HITL
   routing all share that single reducer.
3. **Version** — promoted artifacts are immutable `vN.py` files with a
   manifest `current` pointer recording candidate hash, spec revision, and a
   promote/rollback log. Rollback moves the pointer; nothing is deleted.

## The harness is an agent runtime, not a model API

DSH (DeepSeek Harness) is an agent runtime. The supervisor therefore drives
harnesses through a **process-level contract**, not a chat endpoint:

1. the supervisor writes the task brief to `<workspace>/instructions.md`
2. it launches the runtime process with the workspace as its working
   directory
3. the runtime — an autonomous agent session with its own model calls, tools
   and side effects — writes `candidate.py` into the workspace and exits
4. the runtime's own opinion of its success is **never used**; the
   out-of-process verifier is the only judge

`DSHHarness` launches the real runtime via a pinned command
(`LOOPGRAPH_DSH_CMD`, e.g. `dsh run --workspace {workspace} --brief
{instructions}`) — flags are configured, never guessed — and journals the
runtime's `--version` probe with every run, so a journal always shows exactly
which DSH build produced a candidate. Any other CLI runtime (Claude Code, a
local agent) plugs in through the same `CliAgentHarness` seam. The mock
runtime lives behind the same subprocess boundary. A failing real harness is
a **failed run**, loudly — never converted into a mock or simulated result.

## Quickstart (offline)

```bash
# 1. Start a run. --step 3 executes 3 graph nodes then EXITS THE PROCESS,
#    to show that state survives process death.
python -m loopgraph run demo/slugify.json --harness mock --step 3
python -m loopgraph status  <run_id>
python -m loopgraph resume  <run_id>     # continues exactly where it stopped

# 2. The verified candidate passed the hidden holdout and is parked at the
#    HITL gate. Reject once (your note becomes agent feedback), then approve.
python -m loopgraph reject  <run_id> --note "keep it stdlib-only"
python -m loopgraph approve <run_id>     # -> promoted as v1

# 3. Versions, audit, rollback.
python -m loopgraph versions slugify
python -m loopgraph rollback slugify
python -m loopgraph history  <run_id>    # full event journal
python -m loopgraph spec     slugify     # registered LoopSpec revisions
```

Unknown-outcome demo (the part that must *not* self-heal):

```bash
LOOPGRAPH_CRASH_AFTER_INTENT=1 python -m loopgraph run demo/slugify.json --harness mock
# process dies while a runtime invocation is (possibly) in flight
python -m loopgraph resume <run_id>          # parks; does NOT retry
python -m loopgraph resolve-effect <run_id> --outcome not-executed
```

With a real DSH runtime installed:

```bash
export LOOPGRAPH_DSH_CMD='dsh run --workspace {workspace} --brief {instructions}'
python -m loopgraph run demo/slugify.json    # DSH selected automatically
```

## The graph (demo spec)

```
            ┌──────────── fail (verifier feedback) ───────────────┐
            │                                    ┌── holdout-fail ┤ (generic msg only)
   ┌────────▼───────┐      ┌──────────┐  pass   ┌┴─────┐ approve  ┌─────────┐
   │ generate:agent ├─────▶│  verify  ├────────▶│ hitl ├─────────▶│ promote │
   └────────▲───────┘      └──────────┘         └──┬───┘          └─────────┘
            └───────────── reject (note becomes feedback) ┘
```

## Boundaries, and how each is enforced

| Boundary | Mechanism |
|---|---|
| **Observable** | Every transition is a journal event: node starts, effect intents/results, verifier results (full), holdout results, HITL requests/decisions with actor, promotions. `status` / `history` render it. |
| **Pausable** | `pause` control flag honored at node boundaries; `--step N` bounds any drive; both leave fully journaled state. |
| **Recoverable — and honest about it** | State is replayed from the journal, so kill-and-resume works anywhere *between* effects. A crash *during* a runtime invocation is different: the intent has no recorded outcome, the reducer derives `unknown_outcome`, and both `drive` and `resume` **refuse to re-execute**. A human inspects the workspace and resolves (`not-executed` → safe re-run; `completed` → recover `candidate.py` as the result). Both resolutions are journaled with the acting user. |
| **No self-reported success** | The runtime cannot mark anything done. The verifier runs the candidate in a separate interpreter with a timeout; the holdout gate re-runs hidden cases; `promote` re-checks all preconditions itself instead of trusting the path that reached it. |
| **HITL that actually authorizes** | An approval request carries `request_id` + candidate SHA-256 + spec revision. A decision is refused if the workspace candidate no longer matches the hash the human was shown; the decision event records the binding and the acting OS user; `promote` independently re-verifies approval-hash == current-candidate-hash. |
| **Anti-overfitting** | Specs declare `holdout_tests` that never appear in the brief. On holdout failure the agent receives only a generic "do not overfit" message — the hidden inputs/expectations stay out of the loop — so a holdout pass is evidence of generalization, not memorization. |
| **Version promotion & rollback** | Immutable versions + manifest pointer + audit log; promotion is idempotent per run (crash-safe); rollback never crosses tasks and never deletes. |

## What CI does and does not prove

CI (`.github/workflows/ci.yml`) runs **exactly the local gates** — `ruff`,
`mypy`, `pytest` at pinned versions (`requirements-dev.txt`) — plus an
offline end-to-end loop smoke using the mock runtime.

That proves the supervisor's boundaries. It does **not** prove DSH behavior:
there is no DSH installation in CI, and this project does not pretend
otherwise. Real-DSH evidence is designed to be *per-run and journaled*
instead: every run records the runtime's version probe (`HARNESS_PROBED`)
and every invocation's command, exit code, duration and output tails
(`EFFECT_RESULT`), so a journal from a real DSH run is self-authenticating.
The mock's self-improvement is likewise scripted by construction — it
demonstrates the *loop mechanics*, and is labeled `mock` in every event it
touches; no claim of learning is made from it.

## Layout

```
loopgraph/
  spec.py          LoopSpec: canonical form, sha256 identity, SpecStore
  journal.py       append-only event log (JSONL)
  state.py         pure reducer: journal -> run state (incl. unknown_outcome)
  harness.py       agent-runtime adapters: DSH (pinned cmd), generic CLI, mock
  mock_runtime.py  scripted stand-in runtime behind the same subprocess seam
  verify.py        out-of-process test runner (visible + holdout suites)
  versions.py      immutable version store: promote / rollback / manifest log
  supervisor.py    graph interpreter, effect boundary, HITL binding
  cli.py           run/resume/pause/approve/reject/resolve-effect/status/
                   history/runs/versions/rollback/show/spec
demo/              demo LoopSpecs (HITL and auto-approve variants)
tests/             unit + CLI integration tests (all offline)
docs/DECISIONS.md  design log: the tradeoffs, in order
```

## Known limitations

- Single-box, single-writer per run; the unknown-outcome derivation assumes
  no concurrent driver of the same run. Next step: a writer lease.
- The OS username on decisions identifies, it does not authenticate. The
  binding (decision ↔ candidate hash ↔ spec revision) is the load-bearing
  part; a real deployment would put an authenticated identity in the same
  field.
- The verifier isolates by subprocess + timeout only; untrusted candidates
  would need a container/microVM at the same seam (`verify.py`).
- Journal appends are O(n) per event (seq recount); fine at demo scale,
  trivially replaced by a kept counter or SQLite for long runs.
