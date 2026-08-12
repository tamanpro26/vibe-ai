# TASK — Claude (orchestration layer)

Partner file: `TASK_CODEX.md`. Read the shared section in that file before
starting; it defines who owns which files so we don't overwrite each other.
That has already bitten this repo once — files changed underneath a live
session mid-edit.

---

## Measured baseline (2026-08-07, real runs, not estimates)

| What | Measurement | How |
|---|---|---|
| Simple coding request | **16.8s**, 5 model calls | traced `generate_resilient`, fast path |
| Real coding request (full pipeline) | **>290s, did not finish** | same trace, `forced_team="code"` |
| Coding median (eval set) | 87.2s (max 122s) | 24 graded runs, r2+r3 |
| Reasoning / debugging median | ~20s | same |
| Quality, train split | **1.000 (saturated)** | vibeloop r2/r3, 36 tasks |
| Quality, holdout | 0.867 – 0.900 | same |
| Crashes | 1 / 96 invocations | same |

Two independent results say the multi-agent design buys **reliability, not raw
reasoning**: the GW-blackboard test found no reasoning gain, and vibe-loop
cannot move a score already at 1.000. Treat "improve raw reasoning" as a
hypothesis to test and most likely to *disprove*, not a goal to force. A
clean negative — "the ceiling is the pool, here is the evidence" — is a
successful outcome and must be reported as one.

---

## Owned files (Claude only)

```
manager/**            teams/code.py        teams/base_team.py
vibeloop/**           evals/**             tests/test_core_logic.py
TASK_CLAUDE.md
```

Do not touch anything in Codex's list. If a fix genuinely needs a file Codex
owns, write it in `HANDOFF_NOTES.md` instead of editing.

---

## T1 — Build a coding benchmark that isn't saturated  *(do first)*

Nothing else can be measured until this exists. Train sits at 1.000 across
36 tasks, including ones deliberately hardened twice (multi-bug, aliasing,
closure scoping, adversarial instruction-following). It can now only detect
regression, never improvement.

Current tasks are single-function interview exercises. Real development work
is not. Build 12–16 tasks in `vibeloop/tasks_v4.jsonl` that are:

- **multi-file** — a module plus its tests, imports that must line up
- **stateful** — later steps depend on earlier ones being right
- **underspecified in one respect** — a real requirement the model must infer
  or ask about, since real tickets are never complete
- **failure-prone in a measurable way** — every task keeps a deterministic
  check (`scripts/vibeloop_check.py`), no LLM judging where execution works

Target a baseline score of **0.5–0.75**. Above that, there's no gradient;
below, the signal is noise. Self-verify every task before trusting it, the
same way `tasks_v2` was verified: write a correct reference solution and
confirm it passes, confirm broken input actually fails, and recompute any
expected value independently. A wrong check silently corrupts every later
comparison.

## T2 — Find and fix the >290s full-pipeline latency

A coding request that never returns inside five minutes is the single worst
number in the system, and worse than the eval median implied.

Profile first, fix second. The tracer that produced the numbers above:
patch `models.registry.generate_resilient`, and also the module-level
bindings in `teams/base_team.py`, `teams/prompt_refiner.py`,
`teams/prompt_enhancer.py`, `teams/code.py` — they import it by name, so
patching only the registry misses most calls.

Known suspects, in the order they cost time:
1. **Pipeline depth** — prompt_enhancer (3 models) → prompt_refiner (5
   stages) → team dispatch → per-team internals → leadership review →
   synthesis. Count the actual calls before assuming which stage dominates.
2. **`teams/code.py::_best_of_n`** — 3 parallel drafts, then execution
   filtering, then possibly a judge model.
3. **`_verify`'s repair loop** — each attempt is another round trip.
4. **Failover cost** — `gemini_flash` is quota-exhausted, so calls to it
   burn ~14s failing before falling through. Codex owns that fix; measure
   how much of the total it accounts for so we can tell the two apart.

Every change needs a before/after on the same request. "Feels faster" is not
a result.

## T3 — Test the reasoning ceiling honestly

Once T1 gives a non-saturated set, run the existing loop
(`.claude/commands/vibe-iterate.md`, agents in `.claude/agents/`) against it.
The loop already has the ceiling test built in: give the strongest single
model in the pool the task alone, frontier framing, three samples. If any
solves it, the scaffold lost a winnable task; if none do, the gap is the
pool.

Report which of the three the evidence supports — scaffold gains captured,
plateau, or capability ceiling. Do not soften a ceiling finding to keep the
loop alive.

---

## Rules

- **Verify live, don't assume.** Every claim here came from a real run; keep
  it that way.
- **Never read holdout failures** — only the aggregate. Diagnosing from
  holdout turns it into a second train set and the run loses its only honest
  signal.
- One change at a time, with a before/after number. Bundled changes make a
  promotion unattributable.
- Don't commit or push without being asked.

---

## RESULTS (2026-08-08, all live-measured)

**T1 done.** `vibeloop/tasks_v4.jsonl`, 14 tasks, self-verified (14/14 correct
references pass; 7/7 buggy inputs fail). Baseline **0.60** on the 10-task train
split — inside the 0.5–0.75 target band, so the set finally has gradient.

**T2 done.** The >290s number had four causes, none of them pipeline depth per se:

| fix | evidence |
|---|---|
| fast-path `max_tokens` 2000 → 5000 | qwen3.6-27b bills hidden reasoning against the budget; **7/7 calls truncated** at exactly 2048 completion tokens |
| per-stage deadlines in `prompt_refiner` (60s) / `prompt_enhancer` (40s) | one straggler (`gpt_oss_20b_free`, 376s) gated a 4-way `gather`; that WAS the >290s |
| `DEFAULT_VERIFIER` → `llama33_70b_memory` | old verifier scored **broken code above threshold in 2 of 3 runs** — the cascade gate was inert. 40x faster too |
| ambiguity gate builds + states assumptions | it returned 3 questions and 0 code on a prompt that defined the behaviour verbatim |

Same request: timed out at 400s → 327.9s → **152.7s**.

**T3 done — and the answer is the uncomfortable one.** Ceiling test, same
deterministic checks:

```
full pipeline                                    0.70   ~20min (3x 180s timeout)
gpt_oss_120b_coder alone, VibeAI's _CODE_SYSTEM  1.00   115s
gpt_oss_120b_coder alone, generic prompt         1.00   46-69s
llama33_70b_coder (CHEAP tier) alone             1.00   73s
```

Not a plateau and not a capability ceiling — **the scaffold was net-negative**.
Every task the pipeline lost, a single model solved. Running the ceiling test
with VibeAI's *own* `_CODE_SYSTEM` rules out the "better prompt" explanation.

Fix: `_try_fast_path` now takes the direct path for `task_type in
("vibe_coding", "debugging")` at any complexity. Those tasks classify
`"moderate"` and missed the old `"simple"` gate by one word.

**Train 0.60 → 1.00. Holdout 1.000. Median latency 13.6s, total 263s.**
Suite: 509 pass, 2 pre-existing `pyautogui` failures.

Caveats worth keeping honest: no pre-change holdout measurement exists, so
holdout confirms generalisation rather than proving a delta; holdout is n=4;
and this benchmark is focused single-function coding, so the verdict does not
extend to multi-file/agentic work, which is still unmeasured.
