---
description: Run one iteration of the VibeAI scaffold-optimization loop — diagnose, mutate, gate, promote or reject, report.
argument-hint: [run-dir, default runs/r1]
allowed-tools: Bash, Read, Agent
---

Run **one** iteration of the VibeAI scaffold-optimization loop against
`$1` (default `runs/r1`).

One iteration per invocation. Do not loop internally — the user decides whether
to continue, and a command that quietly runs six iterations spends six
iterations' budget on one instruction.

## Setup

```bash
VL="$HOME/.claude/skills/vibe-loop/scripts"
RUN="${1:-runs/r1}"
```

If `$RUN` does not exist, stop and tell the user to run the skill's setup first
(`loop_state.py init` + a baseline eval on both splits). Do not initialize a run
yourself as a side effect of an iterate command — the task split is created once
and must never be reshuffled.

## What this loop does and does not do

It optimizes the **scaffold**: prompts, routing, council parameters, verifier
thresholds, context handling, retry policy. It does not train models. VibeAI runs
on free-tier endpoints whose weights are not ours.

Orchestration reliably improves *reliability* — how often a good answer comes out
— while leaving the *ceiling* near where the base models put it. So a conclusion
of **"this is a capability ceiling, stop iterating"** is a successful outcome and
must be reported as one, not treated as the loop failing.

## Pipeline

Run these in order. Every arrow marked ↓ is a stop condition — honour it and do
not continue down the pipeline.

### 1. Budget check

```bash
python "$VL/budget.py" check --run-dir "$RUN"
```

↓ **blocked** → report the handoff and stop. Do not start an iteration that
cannot be afforded; a half-funded iteration produces a partial result that still
costs full money.

### 2. failure-diagnostician (opus)

Pass it `$RUN` and the current iteration number. It returns the target class,
hypothesis, and kill criterion, and writes `mutation.json`.

↓ **`recommend_stop: true`** → report the ceiling finding and stop. Do not
mutate. This is a real result; deliver it as one.

### 3. scaffold-mutator (sonnet)

Pass it `$RUN`, the iteration number, and the diagnostician's JSON. It writes
≤2 policy-level edits into `iterations/iter-NNN/candidate/`.

↓ **`refused: true`** → report the refusal reason and stop. A refusal means the
hypothesis could not be tested inside the allowed surfaces; that is information,
not an obstacle to route around.

### 4. loop-tester (haiku)

Pass it `$RUN` and the candidate path. It runs gates 1–3.

- ↓ **`unhealthy`** → invoke **repair-medic** (sonnet) with the bug signature.
  If it returns `halted: true`, report the `HUMAN_NEEDED.md` handoff and stop.
  Otherwise the iteration ends here — re-run the command to try again.
- ↓ **`abort_early`** → discard the candidate, report, stop. The next invocation
  mutates the champion again, never the rejected candidate.
- ↓ **`blocked`** → report and stop.
- **`healthy_no_gain`** → continue to the gate; a no-gain candidate still gets
  gated so the result is recorded rather than assumed.
- **`healthy_improved`** → continue.

### 5. Gate

```bash
python "$VL/loop_state.py" gate --run-dir "$RUN"
python "$VL/loop_state.py" stop --run-dir "$RUN"
```

Promotion needs a holdout gain above `min_gain` that survives a paired bootstrap.
Rejected candidates are discarded.

### 6. tier-comparator (opus) — conditional

Run **only** when the candidate was promoted **and** loop-tester set
`recommend_comparison: true`.

It is the most expensive step in the pipeline and only says something meaningful
about a candidate that genuinely moved. Running it on every healthy iteration
roughly doubles loop cost for no added information.

### 7. Report and record

```bash
python "$VL/report.py" --run-dir "$RUN" --out "$RUN/report.md"
python "$VL/budget.py" spend record --run-dir "$RUN" --iteration <n> --calls <actual>
```

Record **actual** calls, not an estimate. The budget check in step 1 is only as
honest as this number, and a run that under-reports spend will sail past its
ceiling.

## Sub-agent messages are JSON only

Every sub-agent returns structured JSON — never transcripts, raw model answers,
or logs. Carrying raw answers through this orchestrator's context on every
iteration is the largest avoidable cost in a multi-agent loop, and it compounds:
the context persists across the whole pipeline.

If you need to see a specific answer, read the row from the results file
yourself, at the moment you need it.

## Two invariants that bind you as well as the sub-agents

**Never read holdout failure rows** — only its aggregate score. Diagnosing from
holdout turns it into a second train set, and the loop permanently loses its only
honest signal. This applies to you, not just the diagnostician: do not open
holdout rows to "help" a sub-agent that seems stuck.

**Read the answers behind any large jump.** A big score gain from a small edit
usually means the mutation found the checks rather than improving behaviour. When
a delta looks too good, open the rows that moved before promoting.

## Report to the user

Keep it short — five lines at most:

- iteration number and the failure class targeted
- what changed (one line)
- train delta, holdout delta
- promoted or rejected
- what is next, or the stop finding

On a stop, give a straight answer on which of these the run found:

- **Scaffold gains available and captured** — holdout improved, more likely
  remains.
- **Plateau** — the current mutation surface is exhausted; further gains need a
  different surface (new tools, retrieval, better routing), not more iterations.
- **Capability ceiling** — most remaining losses are unreachable by any scaffold.
  Say so directly. The honest options are a stronger model in the pool, a
  narrower product scope, or accepting the gap.
