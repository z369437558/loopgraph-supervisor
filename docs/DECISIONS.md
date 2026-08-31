# Design log

Short records of the decisions that shaped this implementation, in order.
The commit history mirrors them.

## 1. Event sourcing over mutable state

One reducer (`state.py`) is the single definition of "where is this run".
Recovery, resume, status and HITL routing are all the same code path, so the
recovery path cannot rot separately from the happy path. Rejected
alternative: a mutable `state.json` snapshot — simpler writes, but recovery
then depends on the snapshot being written at exactly the right moments,
which is the bug class this design exists to eliminate.

## 2. Harness = agent runtime, invoked at the process boundary

First cut modeled the harness as a chat-completions adapter. That was wrong:
DSH is an agent runtime, and a runtime has its own tools and side effects.
The correction has consequences beyond naming — the supervisor's contract
became filesystem + process (brief in, artifact out, exit code), which is
what makes the abstraction genuinely harness-neutral (any CLI runtime fits)
and what makes the unknown-outcome problem real rather than theoretical.
Rejected alternative: keeping a "model API" harness variant alongside — it
would blur the boundary the design is supposed to demonstrate.

## 3. Exact runtime pinning is configuration + evidence, not a lockfile

The runtime is an external binary, so a Python lockfile cannot pin it.
Instead: the launch command is explicit configuration (`LOOPGRAPH_DSH_CMD`,
never guessed), and every run journals the runtime's version probe and every
invocation's command/exit/duration/output. The pin lives in the audit trail
of each run, which is stronger than a repo-level declaration that CI cannot
check anyway. Dev tooling (ruff/mypy/pytest) *is* pip-installable and is
pinned exactly in `requirements-dev.txt`.

## 4. Unknown outcome parks; a human resolves

At-least-once retry is fine for pure steps and wrong for external effects: a
crash mid-invocation means the runtime may or may not have acted. The
journal therefore records intent *before* launch; a dangling intent derives
`unknown_outcome`, and nothing in the system will re-execute it. Resolution
(`resolve-effect`) is explicitly human: declare `not-executed` (re-run is
safe) or `completed` (recover the workspace artifact). Rejected alternative:
idempotency keys with automatic retry — correct for idempotent APIs, but a
generic agent runtime gives no such guarantee, and pretending it does is how
double execution happens.

## 5. Approval is a binding, not a flag

An approval that isn't bound to content approves whatever happens to be
there when it lands. `HITL_REQUESTED` therefore carries the candidate's
SHA-256 and the spec revision; the decision is refused if the candidate
changed; the decision event records the binding and the acting user; and
`promote` re-checks the binding itself. Defense in depth over trust in
control flow.

## 6. Holdout stays hidden, or it proves nothing

If holdout cases (or their failures, in detail) reach the agent, they are
just more visible tests and "passing holdout" is memorization. On holdout
failure the loop receives only a fixed generic message. The full holdout
results are journaled — for the human, who decides with more information
than the agent ever sees. Consequence accepted: the agent may loop blindly
against a holdout it cannot see; `max_iterations` bounds that, and a run
that cannot generalize *should* fail.

## 7. The mock is a labeled harness, never a fallback

The mock runtime exists so the loop is exercisable offline and in CI, and it
sits behind the same subprocess seam as a real runtime. It is selected
explicitly, labeled `mock` in every journal entry, and no real-harness
failure ever degrades into it. A failed run is a failed run.

## 8. Workspace and control plane live in separate trees

First layout put the workspace inside the run directory — which handed any
real runtime the holdout answers via `../task.json`. The mock never looks,
but the boundary must hold for runtimes that do. Now the runtime's cwd tree
(`workspaces/<run>/`) contains only the brief and the artifact, and no
relative path from it reaches the frozen spec or journal (`runs/<run>/`). A
test walks the workspace tree asserting no holdout value appears. Honest
residual: a filesystem-scanning adversarial runtime still needs an OS
sandbox — stated in the README rather than papered over.

## 9. Scope: the kernel only

No memory, no teams, no SSE, no database. Everything here serves the four
things under test: the LoopSpec object, durable recovery with honest effect
semantics, verification the agent cannot influence, and authorization that
binds. Peripheral capability would dilute the evidence for exactly those.
