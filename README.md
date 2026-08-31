# LoopGraph Supervisor

A **DSH-first, harness-neutral, durably-recoverable** supervisor for agent
loops — an MVP of the RSI (recursive self-improvement) shape: an agent
generates an artifact, an independent verifier tests it, failures feed back
into the next iteration, a human gates promotion, and every promoted version
is immutable and rollbackable.

Pure Python stdlib. No frameworks, no dependencies, no API key required to run
the full demo.

## Quickstart (offline, deterministic)

```bash
# 1. Start a run. --step 3 executes 3 graph nodes then EXITS THE PROCESS,
#    to prove that state survives process death.
python -m loopgraph run demo/slugify.json --harness mock --step 3

# 2. Observe, then resume — it continues exactly where it stopped.
python -m loopgraph status  <run_id>
python -m loopgraph resume  <run_id>
# ... verification passes, the run parks at the HITL gate and exits.

# 3. Human-in-the-loop: reject once (your note becomes agent feedback),
#    then approve.
python -m loopgraph reject  <run_id> --note "keep it stdlib-only"
python -m loopgraph approve <run_id>          # -> promoted as v1

# 4. Versions & rollback.
python -m loopgraph versions slugify
python -m loopgraph rollback slugify          # current pointer moves back
python -m loopgraph show     slugify          # print current artifact
python -m loopgraph history  <run_id>         # full audit trail
```

To run against the real DeepSeek Harness instead of the mock:

```bash
export DEEPSEEK_API_KEY=sk-...     # optionally DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
python -m loopgraph run demo/slugify.json   # DSH is picked automatically
```

## The LoopGraph

A task spec (`demo/slugify.json`) declares a graph of typed nodes; the
supervisor is a generic interpreter over it:

```
            ┌────────────── fail (with verifier feedback) ─────────────┐
            │                                                          │
   ┌────────▼───────┐        ┌──────────┐   pass    ┌──────┐  approve  ┌─────────┐
   │ generate:agent ├───────▶│  verify  ├──────────▶│ hitl ├──────────▶│ promote │
   └────────▲───────┘        └──────────┘           └──┬───┘           └─────────┘
            │                                          │
            └────────── reject (note becomes feedback) ┘
```

- **agent** — asks the harness for a candidate (prompt includes the previous
  attempt + accumulated feedback).
- **verify** — runs the candidate against the task's test cases in a separate
  interpreter process (isolation + timeout); failures become structured
  feedback. `max_iterations` bounds the loop.
- **hitl** — parks the run in `waiting_human` and exits; `approve` / `reject`
  are journaled decisions. A reject's note is injected into the next
  generation prompt — the human is a first-class feedback source, same as the
  verifier.
- **promote** — copies the verified candidate into the immutable version store
  and advances the `current` pointer.

## How each requirement is met

| Requirement | Mechanism |
|---|---|
| **Observable** | Every transition is an event in an append-only `journal.jsonl`. `status` renders graph position; `history` renders the full audit trail (generation, test results, edges taken, human decisions). |
| **Pausable** | `pause` writes a control flag honored at the next node boundary → `RUN_PAUSED` event. `--step N` additionally bounds any drive to N nodes. |
| **Recoverable** | Run state is **never held in memory as the source of truth** — it is a pure fold (`state.replay`) over the journal. Kill the process anywhere; `resume` replays and continues. Node execution is at-least-once and safe to repeat. |
| **HITL** | The `hitl` node type; decisions are journaled events that route the graph (`on_approve` / `on_reject`). |
| **Version promotion & rollback** | Promoted artifacts are immutable `vN.py` files; `manifest.json` keeps a `current` pointer plus a promote/rollback log. Rollback is an O(1) pointer move, never a delete. |
| **DSH-first** | `DeepSeekHarness` is the default whenever `DEEPSEEK_API_KEY` is set (model/base URL overridable via env). |
| **Harness-neutral** | The supervisor depends only on `Harness.complete(system, user) -> str`. DeepSeek, any OpenAI-compatible endpoint, and the offline mock are interchangeable via `--harness`; a run records its harness in `meta.json` so resume is consistent. Adding Claude/Qwen/local = one subclass + one registry line. |

## Design decisions

1. **Event sourcing over mutable state.** One reducer (`state.py`) is the
   single definition of "where is this run". Recovery, resume, status, and
   HITL routing are all the same code path, so there is no way for the
   "recovery path" to rot separately from the happy path.
2. **At-least-once node semantics.** A crash *inside* a node re-executes that
   node on resume. Generation and verification are idempotent-safe;
   promotion only ever appends a new immutable version. This is much simpler
   than exactly-once and honest about what a single-box MVP can guarantee.
3. **The graph is data, not code.** The demo task ships its own graph in
   JSON. Different loop shapes (auto-approve, extra review stages, multiple
   verifiers) are spec changes, not supervisor changes.
4. **Self-contained runs.** Each run directory holds its task spec copy,
   meta, journal, and candidates — a run's semantics can't be changed
   mid-flight by editing the original task file, and any run is portable and
   auditable after the fact.
5. **Verification is out-of-process.** Candidates run in a subprocess with a
   timeout. For untrusted/production use this boundary would become a
   container or microVM; the seam is already in one place (`verify.py`).

## Layout

```
loopgraph/
  journal.py     append-only event log (JSONL)
  state.py       pure reducer: journal -> run state
  harness.py     Harness protocol + DeepSeek (DSH) + OpenAI-compatible + mock
  verify.py      out-of-process test runner -> structured feedback
  versions.py    immutable version store, promote / rollback / manifest log
  supervisor.py  the graph interpreter (drive loop) + run lifecycle
  cli.py         run / resume / pause / approve / reject / status / history /
                 runs / versions / rollback / show
demo/slugify.json  demo task: goal + tests + its LoopGraph
runs/              one self-contained directory per run (created at runtime)
artifacts/         per-task version stores (created at runtime)
```

## Known limitations / next steps

- Single-box, single-writer per run (no locking across concurrent drivers of
  the same run). Next: a lease file or SQLite journal with a writer lock.
- The verifier trusts generated code with subprocess-level isolation only —
  fine for a demo, would be a sandbox/container in production.
- "RSI" here improves a *target artifact*; the natural next step is pointing
  the same loop at the agent's own prompt/policy (the task spec already
  supports it: the artifact is just a file and the verifier is just a
  command).
- Journal `append` re-reads the file for the seq counter (O(n) per event) —
  trivially replaceable with a kept counter or SQLite for long runs.
