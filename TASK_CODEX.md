# TASK — Codex (model & provider layer)

Partner file: `TASK_CLAUDE.md`. Claude is working the orchestration layer in
parallel, right now.

---

## Read this first: file ownership

We are editing the same repo at the same time. This has already caused a
problem here — files changed underneath a live session mid-edit, and work
had to be re-verified against a moved codebase. So the split is strict.

**You own:**
```
models/**                     config/models_config.py
core/circuit_breaker.py       config/settings.py
TASK_CODEX.md
```

**Claude owns — do not edit:**
```
manager/**            teams/**             vibeloop/**
evals/**              tests/test_core_logic.py
Neuronova-vibeaiwebsite/**    api/server.py
```

If your fix needs a file Claude owns, do not edit it. Write what you need
into `HANDOFF_NOTES.md` (create it; nobody owns it) and carry on with the
rest. Same in reverse.

Before you start: `git pull`. Before you finish: run the full suite
(`.venv/Scripts/python.exe -m pytest -q`) and confirm you have not broken
Claude's files. Two pre-existing failures are expected and are not yours —
`TestAutomationNonBlocking`, missing `pyautogui`.

---

## The problem, measured

`gemini_flash` (`google/gemini-2.5-flash`) is **quota-exhausted** and has been
all day. A direct probe returns:

```
429 RESOURCE_EXHAUSTED — Quota exceeded for metric:
generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash
```

That is a **per-day** free-tier cap of 20 requests. It does not recover on a
retry, on a backoff, or later in the session. It resets tomorrow.

What it costs right now, measured:

- A trivial coding request spent **14.0s across 3 calls** to `gemini_flash`,
  all of which failed before falling through to a working provider.
- The same 36-task eval set ran with a **median of 12.0s** when Gemini was
  healthy (r2) and **26.5s** when it was exhausted (r3) — total run time
  19min → 29min. **Latency roughly doubled, with no code change.**

So a meaningful share of the system's slowness is not the orchestration at
all. It is repeatedly calling a provider that is known-dead and waiting for
it to fail.

`gemini_flash` is not a minor entry — it is the primary for the brain team
and appears across the refiner stages, so this is being paid on nearly every
request.

---

## C1 — Stop paying for known-dead providers  *(highest value)*

`core/circuit_breaker.py` already trips on failure and already parses the
provider-stated retry delay (logs show `tripped (provider-stated 27s)`). The
gap: a **daily quota exhaustion is being treated as a seconds-scale blip.**
The breaker re-closes after ~30s and the next request pays the full failure
cost again, all day.

Make the breaker able to tell those apart:

- A 429 whose message indicates a **daily / per-project quota** (Google's
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`)
  should open the breaker for hours, not seconds — or until a wall-clock
  reset — instead of the provider-stated retry hint, which for these is
  misleadingly short.
- A 429 that is genuinely rate-limiting (per-minute) should keep the current
  short backoff. Do not collapse the two.

Do not hardcode Google. Parse the signal generically, since the same pattern
appears on other free tiers.

## C2 — Route around exhausted models before calling them

`models/registry.py::generate_resilient` fails over *after* a failure. When
a model's breaker is open on a long timer, it should be skipped in
selection, not attempted and awaited.

Check whether the fallback order consults breaker state at all before
dispatch. If it doesn't, that is the fix: pick the first model whose breaker
is closed.

Keep the existing behaviour when *every* candidate is open — degrade and try
anyway rather than returning nothing. An answer from a struggling provider
beats no answer.

## C3 — Make exhaustion visible

Right now the only way to discover this was a manual probe. Add a cheap way
to ask "which models are actually usable right now" — a function in
`models/registry.py` returning per-model breaker state and the reason.

`api/server.py` already exposes `/api/registry`, but **Claude owns that
file** — do not add the endpoint. Write the function, and note in
`HANDOFF_NOTES.md` that it's ready to be surfaced.

---

## Rules

- **Verify live.** Every number above came from a real run. A 429 is easy to
  reproduce: call `gemini_flash` directly through the registry.
- Leave one runnable check per non-trivial change — the repo's convention is
  a small `test_*.py` or an assert-based self-check, no new frameworks.
  Put yours in a **new** test file, not `tests/test_core_logic.py` (Claude's).
- Report before/after latency on the same request. The target is the ~14s of
  dead-provider waiting, and the 12s → 26.5s median regression.
- Don't commit or push without being asked. If you do commit, keep it to
  your own files so the history stays reviewable.
