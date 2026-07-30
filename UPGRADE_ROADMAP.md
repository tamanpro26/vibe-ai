# VibeAI Reliability Upgrade Roadmap

Written 2026-07-17, after an 8-run live-testing session that found and fixed 4
real bugs (handoff counter reset, no-write nudge wording, Cerebras tools-schema
budget, edit_file failure retry-nudge) and shipped a read-loop dithering guard.
None of the 8 runs finished a multi-page site fully unattended; one needed a
human to cross the finish line. This doc scopes the 7 proposed upgrade areas
against that evidence, before any of them get built.

**How to read this doc:** each area lists what it fixes (tied to an actual
observed failure, not a hypothetical), what already exists in the codebase to
build on, what's genuinely new, a rough effort/risk rating, and dependencies.
The phasing section at the bottom is the actual sequencing recommendation.

**Ground rule carried over from today's session:** verify every claim against
real code before implementing. One of the seven areas' own diagnosis (`Groq
context eviction dropping the task spec`) turned out to be wrong — both
`groq_conn.py` and `cerebras_conn.py` already guarantee the original task
survives truncation (`_pick_anchor_message` / anchor re-insertion, verified
live this session). Two other areas already have real partial infrastructure
(`PROJECT LEDGER`, Groq's `_parse_failed_generation` malformed-call recovery).
Don't rebuild what exists — extend it.

---

## Scoring framework

For each area: **Leverage** (how directly it fixes something we actually
watched fail today) · **Risk** (blast radius / chance of a new bug in
already-fragile code) · **Reuse** (does it extend existing infra or require a
new subsystem) · **Effort** (S/M/L/XL, rough).

---

## 1. Precision: byte-coordinates → semantic coordinates

**Fixes:** the confirmed, reproduced-2/2 `edit_file` `old_str` mismatch — a
weak model guesses at file content instead of reproducing it byte-exact.

**Three sub-proposals, different cost/risk:**

| Sub-proposal | Reuse | Effort | Risk | Notes |
|---|---|---|---|---|
| Auto-repair before bounce-back | New (small) | **S** | Low | Fuzzy-match `old_str` (whitespace-normalized, `difflib` best-window ≥0.9) before returning the hard error. Only surface the error, with a fresh numbered file region, if fuzzy match also fails. Fully isolated to `tools/agent_tools.py::edit_file`. |
| Split decide/apply (fast-apply pattern) | New (medium) | **M** | Medium | Weak model emits intent/sloppy diff; a deterministic applier merges it. Needs a real merge algorithm (AST patch or fuzzy-diff apply) — this is the part of the stack most likely to have its own edge cases (ambiguous matches, multiple candidate locations). |
| Anchor addressing (tree-sitter, `@fn:name` labels) | New (large) | **XL** | High | New dependency (tree-sitter + language grammars), new addressing protocol, new tool schemas, and it changes the entire mental model of how models reference code. Real ceiling-raiser, but it's a new subsystem, not a patch. |

**Recommendation:** ship auto-repair first (S, isolated, immediately testable
against the exact failure we've reproduced twice). Fast-apply is the natural
follow-on once auto-repair's fuzzy-matcher exists — it's the same matching
logic pointed at "intent" instead of "near-miss old_str." Anchor addressing is
a separate, later initiative — don't block the other two on it.

**Dependency:** none (auto-repair). Fast-apply depends on auto-repair's fuzzy
matcher existing first (reuse, don't duplicate).

---

## 2. Goal-tracking: amnesia-first design

**Fixes:** the read-loop dithering pattern (glm_47_flash_zai re-reading the
same files for 6+ iterations) and the "model forgot it was mid-task" class of
failure generally.

**What already exists:** `core/agent_loop.py`'s `PROJECT LEDGER` (line ~712) —
a system-maintained block already re-injected into every prompt, already
carrying open verifier findings and build state, already told to a
taking-over model on handoff ("Check the PROJECT LEDGER above for current
state"). This is proposal 2's "pinned task ledger" in an early form.

**What's net-new:**
- Extend the ledger to carry the full step plan (goal, ordered steps,
  done/pending, current step) — today it only carries findings/build status,
  not an explicit plan.
- **Stateless micro-turns** is a bigger behavioral change: today, one model
  can freely take many turns in a row exploring/deciding on its own. Moving
  to "one call = one ledger-scoped step" means the orchestrator, not the
  model, decides step boundaries — a real control-flow change in
  `AgentLoop.run()`.

**Effort:** Ledger extension — **S/M** (data the ledger carries, not new
control flow). Stateless micro-turns — **L** (changes the actual iteration
loop's contract).

**Risk:** Medium. `AgentLoop.run()` is the same method that's already
accumulated 4 real bugs this session from interacting state (`_no_write_iters`,
`_repeat_count`, `_loop_warnings`, the read-loop guard). Any change here needs
the same isolation discipline the read-loop guard used (one integration
point, own state, documented precedence with existing counters).

**Dependency:** the Definition-of-Done work (#6) should land first or
alongside — the ledger's "step plan" and the DoD's "acceptance criteria" are
naturally the same structured object; building them separately risks two
parallel, drifting representations of "what does done mean."

---

## 3. Tiny context: compile a brief, don't replay history

**Fixes:** the Cerebras/Groq truncation fights from earlier today — "kept
2/9 messages," aggressive truncation eating real history.

**What already exists:** the anchor-guarantee (task survives truncation) and,
as of today's fix, Cerebras's `_truncate()` correctly subtracting tool-schema
cost from budget. What's missing is the "send state, not replay" idea itself
— today's truncation is a sliding window over raw conversation turns, not a
compiled summary.

**What's net-new:**
- **Context brief compilation**: each turn, replace raw history with a
  generated brief (pinned task spec + ledger + file map with hashes + last
  action/result). Needs a cheap model call (or deterministic template) to
  maintain the running brief — this is genuinely new machinery sitting
  between the agent loop and the connector layer.
- **File views, not files**: send the relevant function/section, not the
  whole file. This depends directly on anchor addressing (#1) existing first
  — without stable semantic addresses, "the relevant two functions" has no
  clean way to be identified or sliced.

**Effort:** **L**. This is a real architectural change to how messages get
built (`_build_messages`, `_build_fallback_messages`), not a tweak to
truncation math like today's Cerebras fix.

**Risk:** High if done before #1. "File views, not files" without anchor
addressing means hand-rolling ad-hoc slicing logic (regex/line-range
guessing) that will have its own precision failures — the same class of bug
this whole roadmap exists to eliminate, just relocated.

**Dependency:** anchor addressing (#1) should exist before "file views, not
files" is attempted. Context brief compilation (the summarization half) can
start independently.

---

## 4. Non-determinism: the gap principle + redundancy

**Fixes:** the "8 runs, 8 different outcomes" problem — quality is a function
of which model happens to be holding the task at iteration 15, which is a
function of live rate limits, not task difficulty.

**Two sub-proposals:**

### 4a. Capability passport + "leave the gap empty"

Today's escalation pool (`core/model_escalation.py::agentic_candidates`) will
hand a precision-editing step to a local 0.8B model if that's what's left —
confirmed live (`ollama_escalation_qwen3_5_0_8b` doing a bare `list_dir` when
the real task needed an edit). The fix: a per-model calibration pass (exact-
edit test, tool-format test, instruction-retention test — cheap, deterministic,
run once and cached) sorts models into capability classes, and steps only
fall back **within class**. No qualified model available → the step queues
and waits, rather than silently degrading to something that will just fail
differently.

**Effort:** **M**. The calibration battery is new but small and one-time-run.
The policy change (queue instead of degrade) touches `model_escalation.py`
and the handoff logic in `agent_loop.py` — moderate, bounded change.

**Risk:** Medium — this is a real behavior change (a run can now stall
waiting for a qualified tier instead of always making *some* progress with
whatever's available). Needs the deadline/watchdog work (#5) alongside it, or
"wait" can itself become an unbounded hang.

### 4b. Best-of-3 redundancy for critical steps

**What already exists:** this is not new to VibeAI — `teams/code.py`'s
best-of-N (3 models in parallel, judge picks) and `core/confidence_cascade.py`
already do exactly this pattern for their own scopes. The ask here is to
extend the *same* pattern to precision-edit steps specifically, using it as a
quality lever, not just a cost-control one.

**Effort:** **S/M** — mostly wiring an existing pattern onto a new call site
(the edit-step path in `agent_loop.py`), not new machinery.

**Risk:** Low — reusing a proven pattern.

**Dependency:** 4a and 4b are independent of each other and of everything
else; either can start immediately.

---

## 5. Wall-clock: hedge like Google

**Fixes:** the 110-minute mystery stall in run 1 (a model call that hung with
zero logged activity, no timeout ever firing) and the general ceremony-tax on
every step regardless of risk.

**Three sub-proposals:**

| Sub-proposal | Reuse | Effort | Risk |
|---|---|---|---|
| Hard per-step deadline + watchdog | New (small) | **S** | Low — `asyncio.wait_for` around the call that hung is a bounded, well-understood fix. This alone would have made the 110-minute stall structurally impossible. |
| Risk-tiered verification (skip ceremony on reads) | Existing pattern | **S** | Low — the read-loop guard already distinguishes read vs. mutation; extending "no verifier/leader ceremony on pure reads" is a small, natural extension of logic that already exists. |
| Hedged requests (fire twin request to 2nd provider, take first back) | New (medium) | **M** | Medium — real value, but doubles live API call volume on hedged steps, which matters a lot on quota-scarce free tiers. Needs to be scoped to specific high-value steps, not blanket. |

**Recommendation:** the watchdog/deadline is the single highest-leverage,
lowest-risk item in this entire roadmap — it directly closes a real incident
(the 110-minute stall) with a small, well-understood fix. Do this essentially
immediately, independent of everything else.

**Dependency:** none. This is close to a standalone bugfix, same shape as
today's 4.

---

## 6. Done-detection: ask the artifact, not the model

**Fixes:** both failure directions we saw — premature "verifier says clean"
declarations on a placeholder page, and the iteration-22 mid-sentence death
with no executable exit condition.

**What already exists:** `core/verifiers.py`'s deterministic battery (syntax
checks, TODO-marker detection, incomplete-file detection) and the build gate
(`npm run build`) already form a real, partial Definition-of-Done. What's
missing is (a) the criteria aren't generated per-task up front — they're a
fixed, generic battery — and (b) a model can still just... stop, rather than
being required to pass through a checklist gate to finish.

**What's net-new:**
- **Task-specific acceptance criteria**, emitted once by the planning pass
  (file X exists, build passes, route Y returns real content, no
  placeholder/lorem-ipsum patterns, page body over N chars). This is a
  generation step, not a control-flow change — lower risk.
- **Two-key convergence**: a model can't declare done directly, only call
  `submit_for_review`, which runs the checklist and either ends the run or
  returns the specific failing criteria as the next step. This *is* a
  control-flow change to the loop's exit condition — same risk profile as
  the goal-tracking work in #2, and should share the same "step plan" data
  structure (see #2's dependency note).

**Effort:** criteria generation — **S/M**. Two-key convergence — **M/L**
(touches the loop's exit logic, which today has several different exit
paths — reflexion cap, verifier cycles, hard loop-stop, read-loop escalate/
abort — this would need to unify under one executable gate rather than adding
a 5th parallel exit path).

**Risk:** Medium-high specifically because of that last point — this project
already has 4-5 different ways a run currently ends, found and fixed
incrementally over one session. Unifying them under a single DoD-gate is the
right end state, but doing it carelessly risks becoming bug #9 in the same
mega-method.

**Dependency:** shares its step-plan data structure with #2. Do the criteria-
generation half independently first; do the loop-unification half together
with #2's ledger work, not as two separate refactors of the same method.

---

## 7. Tool-call format failures: make malformed output impossible

**Fixes:** Groq's `400 tool_use_failed` class of error — a format failure,
not a reasoning failure, disproportionately hitting weaker models.

**What already exists:** `groq_conn.py::_parse_failed_generation` already
recovers ONE specific malformed format (`<function=name>{json}</function>`
text instead of a structured tool call). This is a real, working, narrow
version of the "syntax-repair shim" idea.

**Three sub-proposals:**

| Sub-proposal | Reuse | Effort | Risk |
|---|---|---|---|
| Generalize the existing repair shim | Existing (extend) | **S** | Low — broaden `_parse_failed_generation`'s pattern coverage and/or add a cheap deterministic "one bracket away" JSON repair pass before giving up. |
| Grammar-constrained decoding for local Ollama tiers | New | **M** | Low-medium — Ollama supports JSON-schema-constrained output; this is a real, contained win specifically for the local escalation tiers (the 0.8B/3B models), which is exactly where we watched format failures concentrate. |
| A dumber line-protocol for weak tiers | New (protocol) | **L** | Medium-high — this means maintaining TWO parsing protocols (structured JSON tool-calls for capable tiers, a regex line-protocol for weak ones) through the entire tool-dispatch stack. Real conceptual fit with the "match interface complexity to model capability" principle running through this whole roadmap, but it's a parallel protocol to keep in sync indefinitely. |

**Recommendation:** generalize the existing shim first (cheapest, extends
proven code). Grammar-constrained decoding for local tiers next (contained,
directly targets the weakest, most format-failure-prone models). The
dual-protocol idea is worth real design time later, once the capability-
passport work (#4a) exists to cleanly decide *which* tier gets which
protocol — right now there's no clean signal for "this tier needs the dumb
protocol."

**Dependency:** loosely benefits from 4a's capability classes existing first
(to route protocol choice), but the shim generalization can start
independently today.

---

## Phased roadmap

**Phase 0 — do now, independent, lowest risk (matches today's bugfix pattern exactly):**
1. Auto-repair before edit_file bounce-back (§1)
2. Hard per-step deadline + watchdog (§5)
3. Risk-tiered verification — skip ceremony on reads (§5)
4. Generalize the existing tool-call repair shim (§7)

These four are each isolated, individually testable the same way today's 4
bugs were (unit test + a live run), and none depends on anything else in this
document.

**Phase 1 — moderate scope, still fairly isolated:**
5. Capability passport + gap-principle routing (§4a) — needs Phase 0's
   watchdog landed first (waiting-instead-of-degrading needs a deadline or it
   just becomes a new hang).
6. Best-of-3 redundancy for precision-edit steps (§4b) — independent, reuses
   existing best-of-N pattern.
7. Task-specific acceptance-criteria generation (§6, first half) — independent.
8. Grammar-constrained decoding for local Ollama tiers (§7) — independent.

**Phase 2 — the two structural rewrites, done together, not separately:**
9. Ledger → full step plan (§2) + Two-key convergence / unified done-gate
   (§6, second half). These share one data structure and both touch
   `AgentLoop.run()`'s core control flow — doing them as two separate passes
   over the same method risks exactly the kind of drift that caused 4 bugs
   this session. One deliberate refactor, well-tested, not two.

**Phase 3 — the two genuine new subsystems:**
10. Anchor addressing via tree-sitter (§1) — biggest single new dependency
    in this roadmap.
11. Context brief compilation + file-views (§3) — depends on #10 for the
    "file views, not files" half.
12. Hedged requests (§5) — can actually move earlier than Phase 3 if desired;
    it's independent, just deprioritized here because its value (preventing
    another 110-minute-style stall) is already substantially covered by
    Phase 0's watchdog, and it doubles live call volume on quota-scarce infra.
13. Dual-protocol tool calling for weak tiers (§7) — lowest priority; the
    conceptual fit is real but it's the least contained idea in the whole
    roadmap and benefits most from #4a/#10 existing first.

---

## Open questions before Phase 0 starts

- Confirm scope: is Phase 0 the actual next unit of work, or should one of
  these four get picked as a single next step (matching "one change at a
  time")?
- The read-loop guard (`core/read_loop_guard.py`) shipped today already
  established the isolation pattern (one integration point, own state,
  documented precedence with legacy counters) — every Phase 0/1 item should
  follow that same pattern rather than adding more ad-hoc state to
  `AgentLoop.run()` directly.
