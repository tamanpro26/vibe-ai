# Operational decisions & provider facts

Living log of hard-won operational knowledge. When a limit or model changes,
update it HERE and in the one code table that reads it — not in scattered
comments.

## Provider facts (verified live unless noted)

| Provider | Fact | Verified |
|---|---|---|
| Google (Gemini free) | gemini-2.5-flash free tier: ~20 requests/day/model (error body says `limit: 20`) | 2026-06-30, live 429 |
| Groq | `openai/gpt-oss-120b`: 8,000 TPM; `max_tokens` COUNTS toward TPM request size | 2026-07-02, live 413 |
| Groq | `llama-3.3-70b-versatile`: 12,000 TPM + 100,000 tokens/day + 1,000 req/day | 2026-06-30, live 429 |
| Groq | `qwen/qwen3-32b`: 6,000 TPM — and DEPRECATED 2026-06-17 | live 413 + deprecation notice |
| Groq | `meta-llama/llama-4-scout-17b-16e-instruct`: DEPRECATED 2026-06-17 | deprecation notice |
| Groq | Qwen3.x reasoning consumes `max_tokens` even with `reasoning_format=hidden` (0-char outputs on small budgets) | 2026-07-02, live |
| Cerebras | 1M tokens/day free; transient `queue_exceeded` under load (recovers in seconds) | 2026-06-30, live |
| OpenRouter free | ~50 req/day (1,000 with one-time $10 deposit); observed 41% failure rate under load; catalog churns monthly | 2026-06-30, logs |
| Pollinations | `seedream` model returns HTTP 500 server-side; `flux` works. CDN TLS-fingerprint-blocks Python HTTP clients (empty 200) but allows curl | 2026-07-03, live |
| NVIDIA NIM | ~40 req/min SHARED across the whole key (not per-model, not a published SLA — NVIDIA describes it as traffic-dependent). Catalog slugs shift over time. | 2026-07-06, docs + forum reports |
| Z.AI (Zhipu) | GLM-4.7-Flash / GLM-4.5-Flash free outright; ~1,000 req/day reported, but provider has revised free-tier limits twice in the past year — treat as approximate | 2026-07-06, 2026 trackers |
| Mistral La Plateforme | Free "Experiment" tier ~1B tokens/month, but ToS scopes it to evaluation/prototyping, NOT production traffic; exact rate limits no longer published (check own Admin Console) | 2026-07-06, ToS + docs |
| SambaNova | Skipped — contradictory free-tier status across sources as of 2026-07-06; not worth hardcoding possibly-wrong info | 2026-07-06, not added |

## Architecture decisions

- **Council-first manager**: without a valid `ANTHROPIC_API_KEY`, the Free
  Council IS the manager. No doomed round-trips to invalid credentials, no
  recovery pings (`tools/manager_fallback.py`).
- **Agent primary**: GLM-4.7 @ Cerebras — earned empirically (0 failures over
  67 logged calls; best output quality of live testing). Fallbacks:
  GPT-OSS-120B @ Groq → Llama-3.3-70B @ Groq.
- **Compact context is provider-driven**: any Groq model gets minimal tool
  schema + compacted messages (TPM budgets can't fit the full ~8k-token schema).
  Compact messages MUST carry the agent's own action trace and the latest tool
  output near-verbatim — models loop otherwise (observed: 6x identical calls).
- **Golden scaffolds**: harness overwrites vite boilerplate with known-good
  templates; models only fill content. vite.config.js was corrupted in
  essentially every live run before this.
- **Generated images are localized**: pollinations URLs are downloaded into
  `public/images/` at verification time (via curl — see TLS note above) and
  references rewritten. Hotlinking a generation service = broken images.
- **Honest naming (2026-07-03)**: model_ids renamed to `<real_model>_<provider/role>`.
  The previous ids (kimi_k2_6, glm_5_1, …) described models that were not
  actually being called; fabricated benchmark strings were removed with them.

## 2026-07-05: anti-stall guards (from live exam-rerun testing)

- **design_asset budget (10/task)**: a model called design_asset 94 consecutive
  times with unique descriptions and never wrote a file — the identical-args
  repetition guard can't see "same tool, different args" loops. Hard cap in
  ToolExecutor; further calls get a STOP-and-build error message.
- **No-write-progress guard**: >=4 consecutive tool-using iterations without a
  create_file/edit_file -> nudge; >=6 -> tier handoff.
- **Empty-streak handoff**: 3 consecutive empty/stub responses -> hand task to
  the next fallback tier (observed: GLM returned 5 empties while the old guard
  retried it forever).
- **Loop-stuck handoff**: repetition guard now hands off to the next tier
  instead of ending the run (validated live: GPT-OSS stuck at iter 14 ->
  Llama-3.3 finished the run).
- **Cerebras context flap**: free-tier limit dropped to 8,192 tokens on 07-04
  (error body: "limit is 8192") after being 128k-class on 07-02/03. Connector
  now truncates to a conservative budget, parses the live limit from error
  bodies, retries once, and flips the agent to compact format when the
  discovered limit is tight.
- **Reality note**: end-to-end 9+/10 deliverables require the strong primary
  (GLM-4.7) to stay healthy for the bulk of the run. Under degraded providers
  the guards keep runs alive and productive, but output quality tracks
  whichever model does most of the work.

## 2026-07-06: 4-tier fallback chain + new provider connectors

- **Fallback chain extended to 4 tiers**: `gpt_oss_120b_debug` (Groq) ->
  `llama33_70b_coder` (Groq) -> `glm_47_flash_zai` (Z.AI, same GLM family as
  the Cerebras primary but independent infra) -> `deepseek_v4_flash_nim`
  (NVIDIA NIM, different provider entirely, thin ~40 RPM quota — deepest,
  never primary). Refactored from 2 duplicated hardcoded-tuple constants to a
  single `_FALLBACK_CHAIN` list + `_next_fallback_tier()` helper in
  `core/agent_loop.py`, specifically to avoid repeating the GROQ_TPM-table
  drift bug (same fact duplicated in >1 place silently diverging).
- **Mistral (`codestral_mistral`) is manual-select only**: deliberately NOT in
  `_FALLBACK_CHAIN` or `_LLM_PROVIDERS` (which drives auto-substitution in
  `generate_resilient`) — their ToS restricts the free tier to
  evaluation/prototyping, not production traffic. Mirrors how
  `claude_opus_4_6` is already manual-only.
- **`AsyncOpenAI(api_key=...)` raises at CONSTRUCTION time, not call time**,
  when the key is empty/None (verified directly against the installed
  `openai==2.41.0`). The existing `if not settings.X_api_key: raise
  RuntimeError(...)` checks inside `_call`/`_call_with_tools` assumed
  call-time failure and were dead code for the empty-key case. This became a
  live crash risk once the fallback chain could reach not-yet-configured
  tiers (2 of 3 handoff call sites call `registry.get()` with no try/except).
  Fixed uniformly across all 6 OpenAI-SDK-based connectors (cerebras, google,
  groq, mistral, nvidia, zai): `AsyncOpenAI(api_key=settings.X_api_key or
  "not-configured", base_url=...)`. Construction now always succeeds; the
  intended clean `RuntimeError` still fires at call time.
- **`_should_retry()` fast-path for unconfigured keys**: the clean
  `RuntimeError("... not set ...")` above was being retried 3x with
  exponential backoff (~4s wasted) before the fix, since the existing check
  only recognized `status_code`/`code` attributes. Added an explicit
  `isinstance(exc, RuntimeError) and "not set" in str(exc)` fast path in
  `models/base.py::_should_retry()` — verified live, failure now takes 0.00s
  instead of ~4s. Matters directly because fallback tiers 3/4 hit this exact
  path on any install missing `ZAI_API_KEY`/`NVIDIA_API_KEY`.
- **Groq failed_generation recovery was silently dead code, found by the new
  eval harness's first live run**: `groq_conn.py`'s `_call_with_tools` did
  `body.get("error", {}).get("failed_generation", "")` to recover Llama's
  text-format tool calls from a 400 `tool_use_failed`, but the openai SDK
  already unwraps the outer `{"error": {...}}` envelope before storing
  `exc.body` (`openai._client._make_status_error`: `data = body.get("error",
  body) if is_mapping(body) else body`) — so `exc.body` IS the inner
  `{"message", "code", "failed_generation", ...}` dict directly, and
  `body.get("error", {})` always returned `{}`, discarding every recoverable
  failed_generation and forcing a hard fallback-chain cascade instead of an
  in-place recovery. Fixed to `body.get("failed_generation", "")` directly;
  regression-tested in `tests/test_core_logic.py` with a synthetic exception
  shaped exactly like the SDK's real (already-unwrapped) body.
- **Eval harness built** (`evals/run_eval.py` + `evals/tasks.py`): formalizes
  the manual 2-task exam (design + backend) into `python -m evals.run_eval`.
  Grades only what's mechanical — build/pytest success + the zero-token
  verifier battery — and explicitly does NOT compute a fabricated quality
  score; vision-QA and subjective read-through still need a human/LLM pass.
  A backend task with no test_*.py file at all is scored as a failed
  deliverable, not a no-op pass-through (see next point for why).
  **First live run immediately found two real things**: (1) the Groq
  recovery bug above, and (2) that GLM-4.7 sometimes finishes a backend task
  in 2-4 iterations having written the module but only a `tests/__init__.py`
  stub, no actual test file — an explicit prompt requirement silently
  skipped. Not yet root-caused (could be model variance under the compact
  fallback path vs. a genuine primary-model gap) — flagged here rather than
  fixed blind; worth a few more eval runs to see if it's consistent before
  investigating further.
- **Ollama edge-router cascade actually wired** (`teams/router_team.py`
  `classify_quick`): `qwen3_5_0_8b` / `models/connectors/ollama.py` existed
  in the registry and in this file's own docstring since an earlier session
  but was never actually called — `classify_quick` hardcoded the cloud
  dispatcher regardless. Added `models/connectors/ollama.py::is_reachable()`
  (cached 60s TCP+HTTP probe of `/api/tags`) so classify_quick tries the
  local model first only when it's actually running, with `generate_resilient`
  already providing cross-model failover for hard errors.
  **Reachable is not capable, verified live**: this 0.8B model reliably
  answers a one-line JSON instruction but returned 0 chars, EVERY trial
  (5/5, 3 different system-prompt phrasings, max_tokens from 150 to 2000),
  against classify_quick's real multi-field schema — a "thinking" model that
  never converges to visible output for a schema this size, confirmed NOT a
  token-budget issue (more tokens didn't help). `classify_quick` now retries
  once on the cloud model whenever the local attempt comes back empty, so
  correctness holds, but on this hardware the local tier currently never
  wins for this specific task — it only adds latency before the cloud retry.
  Left wired (harmless, correct, and helps the moment a smaller/different
  local model is swapped in) but not claiming a speed win here; a future
  fix would need either a much simpler proxy schema or a stronger local
  model, not more retries.
  **Second bug found in the process, bigger than this feature**: bounded the
  above latency with `asyncio.wait_for(..., timeout=4.0)` and discovered the
  timeout was silently ignored — a 1.5s deadline let the call run its full
  ~10s. Root cause: `models/base.py::_should_retry()` returned `True` for
  `asyncio.CancelledError` (no `status_code`/`code` -> `None not in (...)` ->
  `True`), so tenacity's retry wrapper caught the cancellation and launched a
  fresh attempt instead of letting it propagate. This means `asyncio.wait_for`
  around ANY `connector.generate()`/`generate_with_tools()` call anywhere in
  the codebase was silently a no-op before this fix, not just for this
  feature. Fixed with an explicit `isinstance(exc, asyncio.CancelledError):
  return False` check ahead of the existing logic; verified live
  (isolated repro: timeout now fires at 1.51s against a call that otherwise
  ran ~10s).

## 2026-07-06: deferred from tonight's idea list, and why

A pasted list of 10 upgrade ideas (from a separate Claude Sonnet conversation)
covered new provider connectors, an eval harness, CePO-style comparison
judging, an ACE evolving playbook, GEPA/DSPy prompt evolution, a trained
reward-model judge, an aider-style repo map, sub-agent spawning, a test-first
loop, and Ollama cascade routing. The first three plus repo map and Ollama
cascade are implemented above. The rest are deliberately NOT implemented
tonight — documented here instead of rushed in, since guessing at their
shape risks repeating the earlier "fabricated specs" mistake this project
already had to recover from once.

- **GEPA/DSPy overnight prompt evolution** — genetic/reflective prompt
  optimization needs many scored rollouts (typically hundreds) to safely
  mutate a prompt without overfitting to a handful of examples, and a
  quota budget to run them. `evals/tasks.py` has exactly 2 tasks right now —
  nowhere near enough statistical signal — and this project runs entirely
  on free-tier daily quotas (Groq/Gemini/OpenRouter), which an overnight
  evolutionary search would burn through in hours, not days. Revisit once
  the eval suite has ~10+ diverse tasks and there's a quota budget set aside
  specifically for this (e.g., a dedicated key, or scheduled during a
  quota-reset window).
- **Full ACE (Reflector/Curator evolving playbook)** — `DECISIONS.md` itself
  already plays the "curated playbook" role today, but by hand: every entry
  above is a human-reviewed fact, written after live verification. Automating
  the curation step (a Reflector that critiques outcomes, a Curator that
  merges insights without bloating or self-contradicting the playbook) is a
  real subsystem, not a bolt-on — and an automated curator that drifts is
  exactly the earlier "two independent tables silently diverging" failure
  mode (see the Groq TPM table history above), just applied to prose facts
  instead of numbers, which is harder to catch. Worth a dedicated design
  pass, not a rushed addition.
- **Trained reward-model judge (Skywork-style)** — needs training data (many
  scored prompt/output/score triples), a training pipeline, and hosting —
  a multi-week ML project on its own, and hosting a custom model directly
  conflicts with this project's free-tier-only infra stance. The
  comparison-based judge shipped tonight (`core/comparison_judge.py`) is
  the explicitly-cheaper substitute CePO itself frames as the alternative to
  a trained reward model — implemented instead of deferred, precisely
  because it needed no training data or hosting.
- **Sub-agent spawning with fresh context** — running independent sub-agents
  per file/subsystem needs workspace-partitioning to avoid write races
  between concurrent edits, a merge/conflict strategy, and proportionally
  MORE API calls (each sub-agent carries its own overhead) — which directly
  collides with the free-tier quota reality that already shapes this whole
  architecture (Gemini ~20 req/day, OpenRouter ~50/day). The current
  single-agent-with-4-tier-fallback design already manages a scarce shared
  quota carefully; adding concurrent sub-agents risks exhausting it faster
  while adding real coordination complexity the project's current task
  scale (single web apps, single backend modules) doesn't yet need. Revisit
  if/when tasks become genuinely parallel-decomposable (e.g. "build the
  frontend AND backend simultaneously") where the coordination cost would
  be justified by real parallelism gains.
- **Formal test-first loop** — partially already covered by
  `_run_build_check`'s pytest support and the eval harness's backend gate
  (a missing test_*.py file now fails the mechanical gate, see above); a
  true test-first loop (write failing test -> implement -> re-run) would
  need the agent to author its OWN acceptance tests before writing the
  implementation, which is a prompting/workflow change worth trying but
  risks the model gaming its own tests (writing trivial tests that always
  pass) without a separate check on test quality — not attempted tonight.

## 2026-07-06: local model swap fixes the router's edge tier

- **Hardware confirmed**: i5-12450H (8C/12T), 16GB RAM, RTX 3050 Laptop GPU
  (4GB VRAM, idle/available), 104GB free disk. Ollama 0.31.1 already running
  with GPU support.
- **Swapped `qwen3_5_0_8b` -> `qwen25_3b_ollama`** (api_model
  `qwen2.5:3b-instruct`, 32,768 context, verified via `ollama /api/show`).
  Chosen after checking current (2026) sources: Qwen2.5-3B has a documented
  JSON-schema-compliance edge and, critically, no "thinking" mode — directly
  avoiding the failure mode that broke the 0.8B model. Verified live: correct
  schema-matching classification for both a trivial prompt and a complex
  multi-requirement one, 1.1-3.4s once warm, one-time ~45s cold start to load
  into VRAM. The router's local edge tier now actually works, not just
  "fails safe."
  Renamed rather than kept the old id pointing at a different model — an
  `api_model` change without a name change is exactly the C4 "model_id
  describes something other than what's actually called" mistake this
  project already had to fix once.
- **GitHub Models investigated and rejected as a new provider**: OpenAI-SDK
  compatible (`https://models.github.ai/inference`, GitHub PAT as api_key,
  the user's existing `GITHUB_TOKEN` would have worked with zero new
  signup), and would have added real model diversity (DeepSeek-R1, GPT-4o-mini,
  etc.) to the fallback chain. Not added: GitHub confirmed (2026-07-01
  changelog) it is fully retiring GitHub Models on 2026-07-30, with brownouts
  already scheduled 07-16 and 07-23 — 24 days out from today. Implementing a
  connector for a service with a public shutdown date under a month away
  would be dead code almost immediately; skipped specifically because this
  was checked before writing any code, not after.
- **Eval suite expanded 2 -> 6 tasks** (`evals/tasks.py`): added a SaaS
  dashboard (different UI patterns: charts, data table, dark-mode toggle),
  a CLI tool (argparse, stdin handling — different domain from a library),
  and a debugging task with pre-seeded, verified-broken code
  (`backend_debug_inventory` — confirmed live the seed fails all 5 tests
  with the exact described bugs, and a correct fix passes all 5). The
  original two tasks only ever exercised "build from scratch"; debugging
  is a distinct capability worth its own gate. Added `EvalTask.setup_files`
  (path -> content, written before the agent runs) to support this.
- **Real scope-creep bug found via the new CLI task's first live run**:
  GLM-4.7 built the correct CLI tool (`csvstat.py`, all 17 tests eventually
  passed) but ALSO created an unrequested `index.html` and burned ~2 minutes
  on failing `design_asset` calls — a backend/CLI task with zero mention of
  a web UI. Root cause is model judgment, not a harness bug (no keyword
  heuristic in `agent_loop.py` matched this prompt). Fixed by adding an
  explicit "this is a pure Python/CLI tool, no web UI, do not call
  design_asset" line to all three backend task prompts; re-run confirmed
  clean (11 iterations, 0 findings, no stray files). Left as a documented
  example of the exact instruction-following gap discussed earlier tonight
  — a stronger model is less likely to invent unrequested scope.
- **Comparison-judge extended to the build-failure gate**: previously only
  wired into the deterministic-verifier fix cycle. Same rationale applies to
  a recurring build failure (a blind "fix these errors" retry already failed
  once) — added a `_build_fail_cycles` counter in `core/agent_loop.py`
  alongside the existing `_verifier_cycles`, calling
  `propose_and_pick_fix_strategy` starting at cycle 2, same pattern, same
  fail-soft guarantees. Not independently live-tested (would need engineering
  a reliably-recurring build failure) — verified by construction, since it
  reuses the exact function already unit- and live-tested for the verifier
  path.
- **Local router tier now genuinely wins, not just fails safe** (see
  qwen25_3b_ollama swap above) — this closes the loop on the Ollama-cascade
  item from the original 10-idea list: it was "wired but not verified to
  help" at the end of the previous session, and is now "wired and verified
  to help" after the model swap.

## 2026-07-06 (evening): the "Minecraft site nobody asked for" incident

A live user run of the Meridian task (marketing site + waitlist backend +
seeded debugging) produced a broken, unstyled Minecraft-hosting page at the
workspace root and Meridian fragments scattered into a leftover project dir.
Post-mortem from logs/vibeai_cli.log — three compounding bugs, all now fixed:

- **Root cause: Cerebras truncation could delete the ENTIRE conversation.**
  `_truncate`'s sliding window keeps the newest messages, then pops leading
  orphaned tool-results; when the tail was all tool results, that emptied the
  window ("kept 0/11" at 18:14:56) and the model was called with only the
  system prompt + tool schemas. No task in context.
- **The model then invented a task from the only example in sight**: the
  `design_asset` schema's example text described "a Minecraft server hosting
  website" — two seconds after the 0-kept truncation, the design team was
  generating Minecraft banners, and the model wrote a full 405-line CraftHost
  index.html. Same signature appeared during the 13:03 eval run. FIXED twice
  over: (1) `_truncate` now guarantees at least one user message survives —
  re-anchors on the first user message (the task), hard-capped at 2,000 chars
  (`_ANCHOR_MAX_CHARS`); (2) the schema example is now theme-neutral
  ("dark abstract hero banner...") so there is no fictional product to copy.
- **Verifier blind spot**: the broken page referenced styles.css/script.js
  that were never created, and nothing flagged it — check_relative_imports
  only reads JS `import` statements, and check_css_classes returns [] when
  there are NO css files at all (the missing stylesheet was itself the bug).
  Added `check_html_local_refs` (HTML href/src -> local .css/.js existence,
  resolving like a static server: beside the file, project root, public/).
  Verified against the actual broken output: flags all 3 real defects.
- **Why "the debugging agents didn't work"**: they never ran. The loop spent
  iterations 1-21 starved (empty responses, no-write nudges, the CraftHost
  detour) and first reached the build gate at iteration 22 — MAX_ITERATIONS —
  so zero fix cycles executed. Same model finished the debugging eval in 4
  iterations that morning with intact context. Context starvation upstream
  masquerades as "bad debugging" downstream.
- **Hygiene note, not yet auto-fixed**: the run wrote Meridian's App.jsx into
  `aurora-site/` — a leftover dir from a July 5 run that the pre-flight
  workspace scan advertised to the model. New projects in a dirty workspace
  risk cross-contamination; consider a fresh workspace per project (or an
  agent-prompt rule to always create a new directory for a new project).

## 2026-07-07: the anchor fix regressed into permanent amnesia — root-caused and fixed

The user re-ran a debugging task the morning after the incident above and it
never converged (repeatedly re-stubbed the same ~8 files, build never passed,
run had to be killed manually). Traced from logs/vibeai_cli.log — the
2026-07-06 anchor fix was WORKING (no repeat of the hallucinated-project bug)
but had a serious side effect nobody caught yet:

- **The anchor was firing on nearly every call, not just the rare case it was
  designed for.** Log showed `kept 1/11`, `kept 1/17`, `kept 1/23`, `kept 1/25`
  — every single truncation call for several iterations straight collapsed to
  JUST the re-anchored task, discarding 100% of the model's own action
  history each time. Root cause: the sliding window kept individual MESSAGES,
  not whole conversational turns. When the newest surviving message was a
  lone tool result (its parent assistant tool_calls message didn't fit the
  budget), the orphan-guard stripped it, emptying the window — and once a
  conversation is big enough that this keeps happening, it happens on EVERY
  call, not occasionally. The existing loop-stuck detector correctly noticed
  and handed off to Groq after 2 such iterations (not "stuck forever" —
  that safety net worked), but the handoff ran into a second, larger, fully
  pre-existing bug:
- **`_build_fallback_messages` (the Groq/compact-mode message builder) never
  showed the model the actual content of files it had written** — only an
  80-char args preview (mostly just the file path) and a one-line tool
  result like "created App.jsx (18 lines)". A model that can't see what's
  currently in a file it wrote 2 turns ago cannot decide whether to extend
  it — it just re-stubs a plausible-looking version from the task
  description alone, every time. This explains the exact symptom: ~8 files
  re-created 3-4 times each, sizes never growing, `edit_file` repeatedly
  failing (a correct `old_str` requires knowing current content).
- **Both fixed**: (1) `cerebras_conn.py` and `groq_conn.py` truncation now
  group messages into TURNS (an assistant tool_calls message plus the tool
  results that immediately follow it) and keep/drop whole turns — a tool
  result and its parent are never separated, so real recent history survives
  instead of being orphaned into nothing. The reactive task-anchor is kept
  as a supplementary safety net (it still appends the original task when no
  turn contains a user message) but is no longer the ONLY thing that
  survives. (2) `_build_fallback_messages` now surfaces the actual content
  of the last 3 create_file/edit_file calls (path + up to 1,500 chars each),
  not just a confirmation string.
- **Verified live**: re-ran a comparable multi-component build (Header/Hero/
  Features/Footer + App.jsx, Vite) end to end. Still handed off from Cerebras
  to Groq (loop-stuck detector fired once, same as before — that's expected
  and fine), still rewrote files a few times as it iterated — but this time
  it CONVERGED: build-verification gate passed at iteration 17, `npm run
  build` succeeds standalone, and the deterministic verifier battery is
  completely clean. The previous run never reached a passing state at all.
- **Lesson for next time a fix regresses**: this is the second live incident
  in as many days from the SAME method (react to a truncation symptom by
  patching the visible failure) — the actual fix both times required
  tracing the full log of a real failing run, not reasoning about the code
  in isolation. `git blame`-style "what changed" is necessary but not
  sufficient here; the message-flow bugs only show up under real multi-
  iteration load with a real, sizeable conversation.

## 2026-07-09: skills system (feature iii from the user's expansion list)

- **`core/skills.py` built**: curated, task-triggered guidance modules
  injected into the agent's context when a task's wording matches a skill's
  trigger regexes (capped at 2 per task so unrelated tasks don't pay context
  budget). Each skill encodes REAL, previously-shipped-and-verified defects
  from this project's own live testing as concrete bad -> good examples:
  - `react-frontend-discipline` — orphaned classNames (the #1 shipped defect),
    no text/logos in AI image prompts (the garbled "MPERIAN" hero), edit_file
    over full-rewrite for styling fixes (the aurora-site className-drift
    loop), no placeholder copy.
  - `python-backend-correctness` — dict.get vs dict[key] (the inventory
    KeyError), iterating dicts yields keys only (the total_items TypeError),
    email-regex edge cases (consecutive dots), never order by coarse
    timestamps (the SQLite CURRENT_TIMESTAMP tie bug).
  - `debugging-methodology` — reproduce first, surgical edit_file over
    create_file rewrite, re-run the original failing command to confirm.
- **Curation rule**: a skill only earns a place once it's caused a real,
  verified defect — this is deliberately NOT a generic best-practices dump,
  which models already know and ignore. The value is in the specificity.
- **Wired into `core/agent_loop.py`** ahead of the workspace scan; fails soft.
  Verified live: a dict-manipulation task triggered the python skill and the
  model produced `setdefault(key, []).append(...)` — the exact safe pattern
  for the exact bug class the inventory exam previously shipped.
- **Deferred from the same feature list, per user's priority choice**: VS Code
  extension (multi-week, separate codebase), self-improvement loop (still
  gated on a bigger eval suite, see GEPA/ACE entry above), voice via Wispr
  Flow (API exists — api-docs.wisprflow.ai, WebSocket+REST — but access looks
  sales-gated, not self-serve; needs the user to confirm they can get a key
  before building against it), change-history (next natural pick; most infra
  already exists in core/activity_log.py). "GooseAI" in the user's
  inspiration list = Goose by Block (github.com/block/goose — YAML recipes +
  MCP extensions), NOT the goose.ai inference host; verified via web search.

## 2026-07-09 (later): debugging pass on the skills system found an interaction bug

User asked for a test-and-debug pass on the new skills work. Happy paths and
edge cases (empty task, regex metacharacters, unicode, 250KB input, trigger
cross-leakage, max-2 cap) all passed. The real find was an INTERACTION bug:

- **Truncation anchor vs. prepended context**: `_build_messages` prepends all
  injected context (skills, workspace scan, repo map, plan spec — routinely
  3-8k chars) BEFORE the task text in the first user message, but both
  connectors' re-anchor logic kept `content[:2000]` — the HEAD. Reproduced
  concretely: under heavy truncation the model was re-anchored onto skills
  text + a directory listing with ZERO task content — a softer relapse of
  the invented-project bug the anchor exists to prevent. Predates the skills
  system (the workspace scan alone could exceed 2k chars) but skills made it
  more likely — and the skills work is what prompted the debugging pass that
  found it. Fixed in BOTH cerebras_conn.py and groq_conn.py with a shared
  `_anchor_content()` head+tail slice (500 head + ~1480 tail): tail because
  the agent path appends the task after context; head because a raw pasted
  task states its imperative first. Regression-tested with the exact repro;
  verified live — the anchor now contains the task (1,985 chars, within cap).

## 2026-07-09 (evening): three more features from the expansion list shipped

- **Change history (ii, logging half)** — `core/change_history.py`: per-
  workspace append-only journal at `.vibeai/history.jsonl` recording every
  create/overwrite/edit/replace/delete with a timestamp and the TASK that
  motivated it (AgentLoop tags the executor with the current task). Torn-line
  resilient (crash mid-write loses one entry, not the journal), fail-soft
  (journal errors never break a run), and `.vibeai/` is hidden from the
  model's own list_dir + the verifier battery so the agent can't read,
  reason about, or corrupt its own history. Read endpoints: `GET
  /api/history` + `format_history()`. The learning half of (ii) — a
  self-improvement loop — stays gated on a bigger eval suite (see the GEPA/
  ACE entry): 6 tasks can't distinguish real improvement from noise.
- **Voice input (iv)** — `tools/voice_input.py` + CLI `/voice [secs|file]`.
  Backend: free Groq Whisper (`whisper_large_v3`, connector already existed)
  instead of the requested Wispr Flow — Wispr's API is real
  (api-docs.wisprflow.ai) but sales-gated, and implementing against a
  guessed endpoint contract is the C4 fabrication mistake again. A
  configured WISPR_API_KEY logs a warning and falls back rather than
  pretending. Mic capture via optional `sounddevice` (clean install hint if
  missing). VERIFIED LIVE without a microphone: Windows SAPI TTS generated a
  spoken WAV -> transcribe_file -> Groq Whisper returned the exact sentence,
  even normalizing "greetings dot py" to "greetings.py".
- **VS Code extension (i, minimal honest version)** — `vscode-extension/`:
  a `@vibeai` chat participant (Chat panel -> local `/api/prompt` -> full
  council pipeline) + right-click "Fix/Explain Selected Code" (sends
  selection with file/language context). Deliberately NO inline ghost-text
  completions: free-tier latency can't make them feel good, and a laggy
  Copilot clone is worse than none. TypeScript compiles clean; module-load
  smoke-tested; the exact fetch path VERIFIED LIVE against the real running
  server (`python main.py serve`) — 200 + a real council answer. Run it:
  F5 in the vscode-extension folder (see its README). Not marketplace-
  published (would need a publisher account + security-model rethink).
- **Bonus find**: the user's `.env` still had `API_HOST=0.0.0.0` from before
  the security review — the server was correctly REFUSING tokenless requests
  (403) exactly as `require_token` is designed to on a non-localhost bind.
  Fixed to `127.0.0.1`. The security gate catching a real stale config in
  live use is the first field validation of that design.

## 2026-07-09 (late): extension made marketplace-grade + installed locally

- User wants the extension searchable/downloadable like other multi-agent AI
  companies' extensions. The identity-bound parts (Microsoft account, Azure
  DevOps PAT, publisher ID) can only be done by the user —
  `vscode-extension/PUBLISHING.md` has the exact 15-minute walkthrough.
- Everything else done and verified: professional manifest (icon — generated
  256px council-network mark —, categories/keywords, gallery banner,
  changelog, license, .vscodeignore), `vsce package` produces
  `vibeai-vscode-0.1.0.vsix` (10.4 KB, 8 files), and it was INSTALLED into
  the user's real VS Code via `code --install-extension` — verified present
  in `code --list-extensions` as `vibeai-local.vibeai-vscode`. The .vsix is
  the exact artifact `vsce publish` uploads.
- Honest caveats recorded in PUBLISHING.md: marketplace users without the
  local Python server get a client with nothing to talk to (listing must
  link the server repo); publisher id in package.json must be changed from
  the `vibeai-local` placeholder to the user's real one; all-rights-reserved
  license is unusual for marketplace tooling.

## 2026-07-09 (night): live "AI team collaboration" flow view

- **`core/collab_viz.py`** — tiny synchronous pub/sub the pipeline emits
  structured events into; any frontend can subscribe (CLI now; the API's
  WebSocket later). Two load-bearing rules: emit() with no subscribers is a
  no-op (instrumentation costs nothing when nobody watches), and a subscriber
  that raises NEVER breaks the pipeline (rendering < the work rendered).
- **Stage events** from explicit instrumentation in claude_manager
  (received -> triage/fast-path -> enhancer -> refiner -> classified with
  team list -> dispatch -> per-team working/review/approved -> synthesis ->
  final). **Model events come free** from the one choke point every model
  call already passes through (activity_log.log_model) — per-model lines
  with role + duration, zero per-team instrumentation.
- **CLI**: ThinkingPanel.feed_event renders the flow (● stages, ↓
  connectors, └ model lines, ✓/✗); once structured events flow, the old
  log-scraping translation is suppressed (kept as fallback if
  instrumentation goes quiet).
- **Verified live twice**: full pipeline run (brain team + review +
  synthesis — instrumented sites all reached) and a fast-path run whose
  captured flow renders exactly the user's requested style.
- **Debugging note for the future**: the first live test "showed nothing"
  because the TEST harness's print() crashed on cp1252 encoding ('●') and
  emit() swallowed it — the fail-soft rule masking a renderer bug is by
  design, but renderers must be encoding-safe (rich Console is; bare print
  on Windows stdout is not). PYTHONIOENCODING=utf-8 for ad-hoc scripts.

## 2026-07-09 (late night): benchmark dashboard, and scope pushback on a pasted strategy doc

User pasted an external strategy/brainstorm document proposing a Meridian
product pivot ("VibeMeridian" session-recording sentiment analysis),
monetization/SaaS packaging, an open-source template business, and some
technical upgrades — then said "apply the changes, debug after."

**Deliberately did NOT implement the pivot, monetization, or template
business**: these are either not code tasks at all (pricing, positioning,
marketing) or multi-week ground-up buildouts requiring infrastructure that
doesn't exist (no session-recording capture pipeline anywhere in this
codebase) — silently starting a week of work that reshapes what Meridian IS
would be exactly the kind of unscoped pivot this project's whole practice
this session has been to avoid. Two items in the doc also directly
contradicted standing decisions made earlier THIS SAME SESSION for reasons
that haven't changed: the self-improving loop (still gated on eval-suite
size — see the GEPA/ACE entries) and "heavy" local-first Ollama (the actual
tested finding was narrower: a small local model handles simple
classification with a fallback safety net, not full agent work).

**Did implement the one genuinely small, bounded, non-strategic item**:
`evals/dashboard.py` — aggregates every historical `evals/runs/*/report.json`
into a pass-rate/timing benchmark, keyed by (task, model) so different
models on the same task compare side by side instead of blending into a
misleading average. Directly extends the eval harness already built rather
than inventing a new "benchmark" concept. Terminal view (rich table) +
static, shareable HTML export — deliberately not a live public leaderboard
server, since hosting/auth for that is a real product decision, not a
default.

**Two real bugs found and fixed via live testing against actual historical
run data** (5 real eval runs from 2026-07-06, one of which was interrupted
mid-execution with no report.json — the aggregator correctly skips it
rather than counting a failure that was never recorded):
1. Rich table default truncation cut every task/model name down to a few
   characters ("backend_..." for all three rows) — useless. Fixed with
   `overflow="fold"` on the identifier columns plus an explicit console
   width.
2. An em-dash in the title string hit the same Windows cp1252 console
   encoding issue already logged for core/collab_viz.py earlier tonight —
   confirmed this is a recurring class of bug on this platform, not a
   one-off; fixed by using a plain hyphen in terminal-facing strings (the
   HTML export keeps real typography, since browsers aren't subject to
   console codepage limits).

## 2026-07-09 (night): installable CLI + workspace switching + multi-chat

User wanted a downloadable CLI with `/`-command workspace choosing and
multiple chats per workspace folder.

- **`core/workspace_session.py`** — `WorkspaceSession` holds the active
  workspace folder + the current named chat, and persists each chat's message
  history to `<workspace>/.vibeai/chats/<slug>.json`. Reuses the `.vibeai/`
  convention already established by change_history.py (already hidden from the
  agent's list_dir + verifier battery), so chat logs never leak into model
  context and travel with the folder like `.git`. Recently-used folders are
  remembered in `~/.vibeai/recent.json` for numbered `/workspace use <n>`.
  Fully fail-soft: corrupt/missing chat file → empty history, never a crash.
  12 offline tests cover switch/new/list/delete/rename, cross-workspace and
  cross-chat isolation, corrupt-file resilience, and recent tracking.
- **CLI wiring** — replaced the single global `_agent_history` list with the
  session (all reads via `_history()`, writes via `_session.append()`), and
  replaced the hardcoded `DEFAULT_WORKSPACE` in `handle_agent_task` with the
  session's current folder. New commands: `/workspace use <path|number>`,
  `/workspace recent`, and `/chat new|switch|list|rename|delete`. The agent-
  mode header now shows the active folder + chat. `clear` and history
  summarization now operate on the current chat. `save/load/list` now act on
  the session's CURRENT workspace, not the fixed default. 3 integration tests
  drive the real cli.py handlers so the wiring can't silently break.
- **Packaging** — `pyproject.toml` now defines `[project.scripts] vibeai =
  "cli:run"` plus flat-layout discovery (`py-modules = ["cli","main"]` +
  `packages.find` including the 9 real source packages, excluding
  tests/workspace/logs/evals/vscode-extension). `cli.run()` is a new argparse
  entry point supporting `vibeai [--workspace PATH | PATH]`. VERIFIED: editable
  install (`pip install -e . --no-deps`) registers `vibeai.exe`, and
  `vibeai --help` runs correctly FROM A DIFFERENT DIRECTORY (proving the
  flat-layout sibling imports resolve when installed, not just from repo
  root). Full `/chat` + `/workspace` flow driven end-to-end against a real
  session: chats keep isolated history, workspace switch resets to a fresh
  main chat, recent list tracks both folders. 113 tests pass; `main.py check`
  still works (editable install didn't disturb the existing workflow).
- **Note**: install went into the user's Python user-site
  (Scripts dir not on PATH — pip warned). That's the intended "downloadable"
  deliverable; to type `vibeai` from anywhere the user adds that Scripts dir
  to PATH, or runs via the full path / `python main.py`.

## 2026-07-09 (late): global CLI access, real-REPL testing, non-TTY fallback

- **"Access from anywhere like claude"** — `claude` gets this from `npm i -g`
  (npm auto-adds its global bin to PATH). The Python equivalent: the
  `vibeai.exe` from `pip install -e .` landed in the Store-Python user-Scripts
  dir (`...LocalCache\local-packages\Python313\Scripts`), which is NOT on
  PATH. Added that dir to the **User** PATH via
  `[Environment]::SetEnvironmentVariable('PATH', ..., 'User')` — the safe,
  non-truncating, System-PATH-untouched method (naive `setx %PATH%;x`
  truncates at 1024 chars and can corrupt PATH; avoided). VERIFIED by
  composing a fresh terminal's PATH from the Machine+User registry values and
  running `vibeai --help` from `%TEMP%` with no path prefix — resolved and
  executed. Takes effect in NEW terminals only. **To undo**: remove that one
  entry from User PATH (System Properties → Environment Variables → User PATH),
  or `pip uninstall vibeai`.
- **The interactive CLI actually works** — tested by driving the REAL REPL
  (`python main.py`) with piped commands: banner rendered, `/chat new`,
  `status` (showing the new Workspace + Chat rows), and `/chat list` all
  behaved correctly. Not a mock — the live loop.
- **Non-TTY input fallback** — prompt_toolkit needs a Win32 console/TTY and
  crashes (`NoConsoleScreenBufferError`) when stdin is piped or the terminal
  is dumb (CI, `echo ... | vibeai`, some embedded consoles). `main()` now uses
  prompt_toolkit only when `stdin.isatty()`, else plain `input()` — so the CLI
  is scriptable, not just interactive. `VIBE_PLAIN_INPUT=1` forces the
  fallback. This is a real robustness fix, not just a test hack.

## 2026-07-09: architecture ideas from ruflo-main.zip (Ruflo / ex-Claude-Flow)

Read for inspiration (user-supplied). Ruflo is a mature agent meta-harness
(100+ agents, swarms, MCP, learning loop). Applicable ideas for VibeAI's
performance, noted for LATER (not implemented — CLI was the priority):
  - **ReasoningBank / trajectory learning**: their "learn from successful
    patterns" is the *disciplined* version of the self-improvement loop I've
    deferred twice — it's backed by a real vector store + pattern matching,
    not naive re-prompting. This is the right shape to revisit once VibeAI's
    eval suite is big enough to separate signal from noise. VibeAI already
    has the substrate (chromadb collective_memory + the new eval harness).
  - **Background workers (12 auto-triggered: audit, optimize, testgaps…)**:
    async, non-blocking improvement tasks that run between/after main work.
    Maps cleanly onto VibeAI's existing verifier battery + could extend it
    (e.g. a background "test-gap finder" worker after a build passes).
  - **Vector memory ANN crossover honesty**: their own audit shows HNSW/ANN
    only wins above ~N=5k-20k and ties/loses at small N. A useful reminder
    NOT to over-engineer VibeAI's memory retrieval for a small corpus.
  - **`ruflo verify` (cryptographic witness)**: proves installed bytes match a
    signed manifest. Overkill for VibeAI now, but the right idea if it ever
    ships as a real distributed package.
  - **Router → Swarm → Agents → Memory → LLMs with a learning loop back to
    the router**: VibeAI already has Router + teams + memory; the missing edge
    is the learning loop feeding routing decisions. Aligns with the deferred
    self-improvement work above.

## 2026-07-10: studied Ruflo, built native adaptive routing memory (learning loop)

User asked to understand Ruflo's (ex-Claude-Flow) techniques from the supplied
zip and, where they don't transfer, build VibeAI-native alternatives. Studied
the REAL source (not README marketing):
  - **SONA optimizer** (`v3/@claude-flow/cli/src/memory/sona-optimizer.ts`):
    after each task record `(task_keywords, agent, success)`; maintain
    `pattern -> confidence` with a BOUNDED update (success `c += inc*(1-c)`,
    fail `c -= dec*c`); new task -> keyword-match -> route to highest-confidence
    agent above a threshold. NOT model training — a confidence-scored routing
    memory persisted to JSON. Plus a Q-learning router (reward +1/-0.5) on top.
  - **ReasoningBank**: extracts reusable step-patterns from successful
    trajectories.
  - **Background workers** (`worker-manager.sh`): interval workers
    (pattern-consolidator 15min, learning-optimizer 30min…) running non-blocking.
  - **Signed trajectories**: Ed25519 + sha256 content hashes = portable
    cryptographic provenance (for cross-machine federation trust).

**Applicability verdict (honest):**
  - SONA routing loop → HIGHLY applicable, built (below).
  - Background workers → partial; VibeAI is single-shot CLI not a daemon.
    Deferred; a post-run async consolidation would be the fit, not a daemon.
  - Signed trajectories → not applicable (single-machine, no federation).
  - Swarm/100+ agents/MCP → VibeAI's team model already covers the useful part.

**This resolves the earlier self-improvement deferral honestly.** I kept
deferring "self-improvement" because I conflated it with model/prompt training
needing a big eval suite. Ruflo shows the tractable, safe version: routing
memory over real-run outcomes — bounded update, abundant signal (every run),
no training. Different, safer thing.

**Built `core/routing_memory.py`** — VibeAI's adaptive routing memory:
  - Learns over a small set of task CATEGORIES (frontend/backend/debug/design/
    data/algo), not raw keyword sets — bounded pattern space (categories x
    models), generalizes across projects. Global memory at
    `~/.vibeai/routing_memory.json`.
  - Bounded confidence (start 0.5, cap [0.05, 0.95], SONA-style update). A
    suggestion fires only above 0.60 AND after >=3 samples — flukes can't
    hijack routing.
  - The success signal ALREADY EXISTED and was thrown away: agent_loop's
    `_build_verified` (build/tests passed). Now recorded against the STARTING
    model. Only UNAMBIGUOUS outcomes are learned: build-passed = success,
    hit-iteration-cap = failure, everything else = skip (learning from noise
    is what makes naive self-improvement dangerous).
  - Wiring: `AgentLoop.run(model_explicit=...)` — the CLI passes False while
    on the DEFAULT model, so a confident memory can pick a better starting
    model; an explicit `/model` choice is NEVER overridden. `/routing` command
    shows what's been learned.
  - **Word-boundary bug found + fixed via live testing**: naive substring
    matching had `'ui'` matching `'bUIld'` and mislabeling a FastAPI task as
    frontend. Single-word keywords now require whole-token match (phrases /
    extensions stay substring). Regression-tested.
  - VERIFIED LIVE end-to-end: a real python+pytest agent run passed the build
    gate and recorded `glm_47_cerebras ✓ for backend` (confidence 0.5→0.55);
    `/routing` renders the learned table. 123 tests pass (11 new for routing).

## 2026-07-10: studied Wingman AI, built budget-aware tool selection (progressive disclosure)

User asked to study wingman-ai-main.zip and improve the team further ("break
the limit"). Studied the REAL source:
  - **Wingman AI** is a voice-controlled assistant for GAMES (keyboard/mouse/
    TTS/game-control, "wingmen" personalities). Most of it is not a coding
    agent's domain, and VibeAI already has the overlapping parts (multi-
    provider abstraction, vision, image-gen, voice input).
  - **The one transferable idea: progressive tool disclosure.** Wingman's
    ToolRegistry (`skills/skill_base.py`) sends the LLM a cheap MANIFEST of
    skills + a `@tool`-decorated auto-schema system, loading full tool
    definitions only when relevant — full capability without paying full token
    cost upfront.

**This maps onto VibeAI's #1 documented limit: the Groq TPM token budget.**
Studying VibeAI's own tool selection revealed a STALE assumption: the code
comment claimed "CORE ≈ 8k tokens, FULL ≈ 13.4k" and therefore the compact
(Groq fallback) path dropped to a fixed 6-tool `TOOL_SCHEMAS_MINIMAL`. Measured
2026-07-10: CORE ≈ 1.5k tokens, FULL ≈ 3.2k. The real Groq fallback tiers
(gpt-oss-120b @ 8k TPM, llama-3.3 @ 12k) can now fit far more than 6 tools —
so compact mode was needlessly stripping `design_asset`, `vision_analyze`,
`git`, `github`, `ssh_*`. A design task that fell to Groq literally could not
generate an image.

**Built `tools/agent_tools.select_tools_for_budget(task, max_tool_tokens)`** —
VibeAI-native progressive disclosure, adapted to its static/deterministic
reality (no LLM round-trip, no change to the fragile loop structure):
  - Always includes the coding essentials (create/edit/read/list/bash).
  - Adds task-RELEVANT optional tools (design_asset for design tasks, github
    for repo tasks, ssh for remote…) scored by keyword signals, greedily
    filled to the model's real token budget.
  - `core/agent_loop._compact_tool_budget(connector)` derives the budget from
    the model's actual TPM (GROQ_TPM / Cerebras discovered limit) minus
    reserves. gpt-oss-120b → 4000, llama-3.3 → 8000.
  - Uses the REAL schema objects (no schema/impl drift — the same anti-pattern
    that caused the GROQ_TPM-table bug).
  - Replaced both `tools=TOOL_SCHEMAS_MINIMAL` sites in the compact path.
    TOOL_SCHEMAS_MINIMAL kept for compatibility but no longer used by the loop.
  - VERIFIED: design task → recovers design_asset + vision_analyze; github task
    → recovers github + git; plain bugfix → essentials only; tiny budget →
    essentials always kept. Live-tested a real task on gpt-oss-120b (compact
    path) — no 413, ran clean. 129 tests pass (6 new).

**Net effect:** compact/fallback mode goes from "degraded, 6 tools always" to
"the right tools for THIS task, up to the budget" — recovering capability that
was silently lost, which directly attacks the token-budget limit behind many
of this project's documented bugs.

## 2026-07-10: broader-pool model escalation (autonomous reach-out beyond the fixed chain)

User request: "if the task given is quite different which cannot be done with
good quality with the assigned ais, then the manager ai council or claude
model can use any other models by itself easily using apis, ollama local
models etc." — i.e. two EXISTING give-up points should reach further before
accepting failure/best-effort, instead of building a third disconnected
mechanism:

  - **Agent loop** (`core/agent_loop.py`): the fixed 4-tier `_FALLBACK_CHAIN`
    exhausting used to mean "all models are unavailable, give up." That's only
    5 specific models failing, not every capable model.
  - **Manager** (`manager/claude_manager.py::_run_team`): `review.action ==
    "ESCALATE"` (quality never cleared the bar after max iterations) already
    existed as a signal — it just meant "return best-effort and stop."

**Built `core/model_escalation.py`** as the shared broader-pool lookup, reusing
rather than duplicating what already exists:
  - Pool = `MODEL_REGISTRY` filtered to `_LLM_PROVIDERS` (models/registry.py's
    existing auto-eligible set) — no new provider invented, no API guessed at.
  - `codestral_mistral` STAYS excluded. Its ToS is eval/prototype only, not
    production traffic, and an automatic escalation call IS production
    traffic — the exclusion reason doesn't change just because the caller
    changed. Verified live: absent from both pools.
  - Two pools with different bars: `agentic_candidates()` (tool-calling
    required — filtered to `code_generation`/`agentic_coding` capability) for
    the agent loop; `single_shot_candidates()` (plain `generate()`, no tool
    execution) for the manager's quality-review path — intentionally wider.
  - Ollama models beyond the static router entry (qwen25_3b_ollama) are
    discovered LIVE via `/api/tags` rather than guessed at, then registered as
    ordinary `MODEL_REGISTRY` entries (`register_dynamic_ollama`) so every
    downstream mechanism (registry.get, activity_log, circuit breaker) treats
    them like any other model_id — no parallel code path. Local models are
    appended LAST in `agentic_candidates()`: no verified agentic tool-calling
    track record in this project, so last resort, not first guess.

**Gap found and fixed along the way:** `OllamaConnector` had no
`_call_with_tools` override, so the base class's default silently dropped
every tool and returned prose only — a local Ollama model in the agent loop
could never actually touch a file. Added native tool-calling (mirrors
groq_conn.py's parsing, minus the Groq-specific text-format recovery), so
`agentic_candidates()` isn't offering a candidate that would silently no-op.

**Wiring:**
  - `agent_loop.py` tracks `_tried_models` (every model_id actually attempted
    this run) and, at the two existing give-up points — "all fallback tiers
    failed" and "loop-stuck, no next tier" — tries `agentic_candidates()`
    before finalizing failure.
  - `claude_manager.py::_run_team` calls the new `_escalate()` method at the
    ESCALATE/max-iters branch: tries ONE model outside the team's own roster,
    re-reviews it, and only swaps it in if the fresh review scores STRICTLY
    higher than what the team already produced — never adopts a different
    answer just because it's different.

**Verified live (not just unit tests):**
  - Forced the primary (glm_47_cerebras) + all 4 static fallback tiers to
    raise on a real `AgentLoop.run()` — escalation reached `gpt_oss_120b_coder`
    (an untried registry slot) and the run completed normally instead of
    "All models are unavailable."
  - `discover_ollama_tags()` against the real local Ollama server returned the
    actual two pulled models; `register_dynamic_ollama()` correctly reused the
    static `qwen25_3b_ollama` entry rather than duplicating it.
  - `ClaudeManager._escalate()`: confirmed both directions — a better-reviewed
    escalation output gets adopted, a worse-reviewed one is discarded (returns
    `None`, caller keeps the original best-effort output).
  - 142 tests pass (13 new: 11 for `model_escalation.py`, 2 for
    `OllamaConnector._call_with_tools`).

## 2026-07-10: VibeMind v1 — Jarvis-style desktop assistant (new project, vibemind/)

User's second "pro-level build": a JARVIS-inspired virtual assistant that can
control the real desktop (open apps, type into them, press keys) — brain =
Gemma 4, other agents = Ollama local models. A prior AI tool (Manus) had
already scaffolded part of this in `jarvis-ai-assistant/` and claimed 4/8
tasks done. Audited that claim before building anything further:

  - DB schema (conversations/messages/tasks/action_logs/agent_status/
    voice_transcriptions) was genuinely solid — kept the table SHAPES.
  - "Backend with AI orchestration" and "tRPC procedures" were NOT actually
    done: `server/routers.ts` was 28 lines of pure auth boilerplate ending in
    `// TODO: add feature routers here`; `agentOrchestrator.ts` existed but
    wasn't wired to anything.
  - Two structural dead ends found: `invokeLLM()` called Manus's own hosted
    gateway (`forge.manus.im`) via a `BUILT_IN_FORGE_API_KEY` only Manus's
    platform injects — dead outside it — and hardcoded EVERY agent to
    `gemini-3-flash-preview` (not Gemma 4, not Ollama). And there was no real
    automation anywhere: the "app-agent" only asked an LLM to narrate
    fictional log lines ("launch_app: vscode"); nothing touched the OS. Also
    architecturally impossible as a plain browser web app regardless — no
    browser can spawn processes or send OS keystrokes.

User chose (given a straight choice): rebuild the brain + automation in
Python reusing VibeAI's own proven stack, keep Manus's React UI/UX direction
as the frontend shell. Net result — new `vibemind/` package:

  - `vibemind/automation.py` — the genuinely new capability: real app
    launching (subprocess, argv-list, never shell=True), real window
    focus (pygetwindow), real keystrokes (pyautogui + clipboard-paste for
    `type_text`, verified more reliable than char-by-char `.write()` for
    punctuation/unicode). VERIFIED LIVE: launched real Notepad, typed a string
    with quotes/symbols, read it back via clipboard round-trip — exact match.
  - `vibemind/brain.py` — Gemma 4 (`gemma_4`, already in MODEL_REGISTRY via
    OpenRouter) plans commands into agent-routed steps; app/web steps run a
    NEW small tool-calling loop (not agent_loop.py's coding-specific
    machinery) driven by Ollama (`qwen25_3b_ollama`), falling back to Gemma 4
    if Ollama isn't reachable; file/code steps reuse
    `core.agent_loop.run_agent()` directly rather than reinventing it.
  - `vibemind/db.py` — aiosqlite, mirroring `core/state.py`'s established
    pattern, NOT the original Drizzle+MySQL scaffold. Dropped `users`/OAuth
    entirely: this is a personal single-user local app, not a hosted SaaS.
  - `vibemind/orchestrator.py` + `vibemind/server.py` — FastAPI (same
    auth/CORS discipline as `api/server.py`, arguably more warranted here
    since this surface can control the desktop, not just a workspace).
  - Frontend: dropped tRPC, Manus OAuth, and Manus-platform-only Vite plugins
    (`vite-plugin-manus-runtime`, its debug-log collector, `manus.computer`
    allowedHosts) — none apply outside their hosted platform. Kept the
    existing shadcn/ui component library and rebuilt `index.css` as a dark
    cyan "holographic" theme (the Jarvis brief), built `JarvisOrb` (pulsing
    orb, framer-motion), and 5 pages: Chat (+ voice via MediaRecorder →
    `/api/voice`), Agents, Tasks, Action Log, History.

**Three real bugs found via live testing, not spec review, all fixed:**
  1. `upsert_agent_status(current_task=None)` was indistinguishable from
     "argument not passed" — clearing current_task after a step finished was
     silently a no-op. Fixed with an `_UNSET` sentinel distinct from `None`.
  2. `_DEFAULT_WORKSPACE = "."` meant the FastAPI process's cwd — the VibeAI
     repo root itself. A live voice command ("create greetings.py") ran
     VibeAI's real coding agent against the live VibeAI codebase instead of
     anywhere safe. Fixed: dedicated `~/VibeMind/workspace/`, auto-created.
  3. Gemma 4's planner sometimes splits one file task into separate
     "write the code" / "save the file" steps, each dispatched as an
     independent `run_agent()` call with NO memory of the other's work — one
     call invented a wrong filename with placeholder content because it never
     saw what the other call actually wrote. Fixed: file/code steps now run
     against the ORIGINAL full command (not the fragment), and a second
     file/code step in the same plan is skipped rather than re-run.

**Known, honest limitation (not hidden):** qwen2.5:3b-instruct (the local
Ollama model driving app/web/file/code sub-agents, per the user's explicit
spec) is not perfectly instruction-following at this scale — verified live
twice: it pressed Enter after being explicitly told not to, and produced a
literal `\n` in file content instead of an actual newline. This is an inherent
trade-off of the requested all-local-model architecture, not a bug in the
integration; a stronger sub-agent model would need to be a deliberate
follow-up choice, not a silent override of what was asked for.

**VERIFIED LIVE end-to-end, real API calls, no mocking, three layers deep:**
Python-direct → HTTP API → browser UI. Real Gemma 4 planning, real Ollama
tool-calling, real desktop actions (Notepad), real SQLite persistence, real
Whisper voice transcription (via VibeAI's existing `tools/voice_input.py`),
all 5 frontend pages screenshotted rendering real data with zero console
errors (aside from a cosmetic missing favicon).

## 2026-07-10: VibeMind v2 — Electron desktop app + full PC storage/apps access

User: "create an desktop app which would be done using electron ... make it more
cooler and better /design-taste-frontend-v1 ... fully connect it with pc
storage system and all the apps." Three things: Electron packaging, a real
design pass, and genuine filesystem/installed-app reach.

**Electron shell (`jarvis-ai-assistant/electron/`):**
  - `main.cjs` spawns the Python backend (`vibemind.server`) as a child process
    rooted at the repo, waits for `/api/health` before showing the window, then
    loads Vite (dev) or the built bundle (prod). Kills the backend on quit so no
    orphan uvicorn. Electron is a SHELL around the existing backend, not a
    reimplementation — the backend stays the single thing that touches models,
    DB, and desktop.
  - `preload.cjs` exposes a minimal, curated bridge (`window.vibemind`:
    pickFolder/pickFile/openPath) with contextIsolation on — the one class of
    thing a browser genuinely can't do (native OS dialogs).
  - `package.json` gained `main`, electron-builder config (NSIS installer),
    and a `dev` script (concurrently vite + electron). Vite `base: "./"` so the
    prod bundle works over file://; frontend API base switches to the absolute
    backend URL in prod (relative /api would 404 under file://); backend CORS
    now also allows the Electron renderer's `null` origin.

**Full PC storage + apps (`vibemind/system.py`):** real, not simulated —
list_drives, list_directory (browse anywhere), read/write_text_file (anywhere),
open_path (default-app open), search_files, and list_installed_apps (enumerates
Start Menu shortcuts). Exposed as FastAPI routes (GET read-only; write/open
gated by require_token) AND wired into the desktop agent's toolset, so the
assistant can browse/open/launch by natural-language command. VERIFIED LIVE:
111 real installed apps discovered, real C:\ drive usage, real home-dir listing.

**Routing fix (found via live test):** "list my home folder" first went to the
sandboxed CODE agent (workspace-only) and failed. Fixed the planner + dispatch
so real-PC file operations ("app"/"web"/"file") all route to the desktop agent
with the system toolset; only "code" (authoring NEW programs) uses the
sandboxed coding loop.

**Design pass (per design-taste-frontend-v1):** applied the skill's anti-slop
rules — banned Inter (now Geist + Geist Mono), killed the heavy neon/outer
glows in favor of "liquid glass" (1px inner highlight + tinted diffusion
shadow), desaturated the single cyan accent (<80% sat), off-black base (no pure
#000), mono tabular numerals for all data, staggered list reveals, tactile
:active feedback, real skeleton loaders + empty states. Added two new pages:
**Files** (drive-usage strip, breadcrumb, folders-first listing, native Browse
button that only appears under Electron) and **Apps** (searchable grid of all
installed programs, click-to-launch).

**Honest scope notes:**
  - Kept lucide-react icons rather than swapping the whole shadcn icon system to
    Phosphor as the skill states — lucide is clean line-SVG (satisfies the real
    "no emoji, use icons" intent), and a full swap would churn every shadcn
    primitive for little gain. A deliberate deviation, noted not hidden.
  - The `ELECTRON_RUN_AS_NODE=1` env var is set in this dev sandbox, which makes
    Electron boot as plain Node (`app` undefined). Launched with it unset for
    verification; a normal user machine won't have it. Not a code bug.

**VERIFIED LIVE (real Electron window, not a browser):** attached Playwright
over Electron's CDP endpoint — confirmed the backend auto-spawned and became
healthy, the window loaded and was already fetching real data, and the native
bridge was truly injected (`window.vibemind.isElectron === true`,
`pickFolder` a real function). Screenshotted the actual Electron window on the
Files page showing the real C:\ drive and home directory. `vite build`
(2127 modules) clean; all pages zero console errors.

## 2026-07-10: polish pass — CLI slash-command palette, real 3D orb, typewriter output

Three targeted tweaks: a Claude Code-style "/" dropdown for VibeAI's CLI, a
genuine three.js orb for VibeMind (the flat CSS/Framer version "didn't look
impressive" per a reference screenshot), and streamed-looking text output for
both apps' AI responses instead of appearing all at once.

**VibeAI: `/` command palette (`cli_completer.py`, new).** A prompt_toolkit
`Completer` wired into `cli.py`'s existing `PromptSession` (`completer=`,
`complete_while_typing=True`). Two levels: typing `/` alone lists every
top-level command with its one-line description, filtered live per keystroke;
typing `/workspace ` or `/chat ` then shows THEIR subcommands (use/recent/
save/load/list, list/new/switch/rename/delete), and `/model ` completes
against the live `MODEL_REGISTRY`. Kept deliberately separate from `cli.py`
(already huge) — pure input-UX, synced by hand with the dispatch table it
mirrors. VERIFIED: called `get_completions()` directly for a dozen inputs
(`/`, `/mo`, `/workspace u`, `/model gl`, `hello`, ...) and confirmed exact
match sets before trusting prompt_toolkit's (already mature) menu rendering.

**VibeMind: real 3D orb (`JarvisOrb3D.tsx`, new).** Installed
`three` + `@react-three/fiber` + `@react-three/drei` + `@react-three/postprocessing`.
Replaced the flat CSS/Framer-Motion orb with an actual WebGL scene: a
`MeshDistortMaterial` core (organic, breathing distortion) inside a rotating
wireframe icosahedron shell, `Sparkles` particles, and `Bloom` post-processing
for glow — same spirit as the reference JARVIS-style orb image. Four
state profiles (idle/listening/thinking/speaking) drive distortion amount,
rotation speed, and particle energy — "thinking" is a genuinely distinct
visual (color shift to violet, heavier distortion, a bright spinning ring
segment appears), not just a recolored idle blob, so it actually reads as
"the AI is processing" at a glance. Isolated in its own `Canvas` + memoized
component per the design-taste skill's performance rule (perpetual animation
must never trigger parent re-renders — `useFrame` already runs outside
React's render cycle here). VERIFIED LIVE: Playwright screenshots of both
idle (cyan, calm) and thinking (violet, distorted, ring visible) states,
confirmed a real `<canvas>` WebGL element renders, zero console errors.

**Both: typewriter output.**
  - VibeMind: new `TypewriterText.tsx` reveals `content` progressively (chars/
    tick scaled so total reveal time is capped ~2.2s regardless of length —
    short replies still read as "typed", long ones don't make the user wait).
    Applied ONLY to assistant messages that arrive while the Chat page is
    open, not to history — a `seenIds` ref is seeded with every message
    already present on first load/conversation-switch, so old messages never
    replay the animation on refetch/poll. VERIFIED LIVE: polled the reply
    bubble's text length every 250ms after sending a real message — samples
    `[45, 45, 45, 8, 22, 33, 44, 44, ...]` (45 = the user's own bubble before
    the reply exists; then genuine progressive growth 8→22→33→44, not an
    instant jump to full length).
  - VibeAI: new `_print_typewriter()` in `cli.py` replaces 4 duplicated
    `if "``` " in text: Markdown(...) else: plain-lines` print sites (agent
    task, `/m` manager mode, `/think`, `/vision`) with one shared helper using
    `rich.live.Live` to redraw progressively, same capped-duration logic.
    Falls back to an instant, un-animated print when stdout isn't a real
    terminal (`console.is_terminal` false, e.g. piped/CI) or `VIBE_NO_TYPEWRITER=1`
    — a piped consumer needs the real text immediately, not a redraw loop it
    can't render. VERIFIED: the non-TTY fallback path directly; the real
    animated path by forcing `force_terminal=True` and confirming each frame
    contains strictly more text than the last, ending in the exact original
    string. 142 existing tests still pass unchanged.

## 2026-07-10: VibeMind — fast path + system profile (root-caused a real 13s delay)

User report: asking to play a specific song on Spotify didn't work, and simple
commands felt slow ("shouldn't be time consuming like 1-2 secs just open an
app"). Investigated rather than assumed.

**Root cause, measured live, not guessed:** a plain "open notepad" took
**12.97s** end to end. Backend log timestamps pinpointed exactly where:
Gemma 4 planning call (OpenRouter) **6.04s**, then two Ollama tool-calling
loop iterations at **4.90s** and **~2.4s** — two full LLM round-trips,
minimum, for an action that is actually just `subprocess.Popen(["notepad.exe"])`.
The architecture (plan via one LLM, execute via a tool-calling loop on
another) is correct for genuinely ambiguous requests, but was being used
unconditionally even for trivial, unambiguous ones.

**Fix: `vibemind/fastpath.py` (new).** A deterministic, zero-LLM regex router
checked BEFORE `plan_task()` in `orchestrator.handle_message()`. Matches only
unambiguous single-action commands -- `open/launch/start <short name>` and
`play <query> [on spotify]` -- and executes them as a direct function call.
Everything else (multi-step, ambiguous, or naming a different app: "play X on
YouTube") falls through to the full pipeline completely unchanged. Verified
the classifier against 9 real cases (simple commands correctly fast-pathed,
complex ones like "open the file at X and summarize it" or "open vscode and
create a new project" correctly NOT matched, so they still get real
planning). Measured result: "open notepad" **0.56s** (~23x faster), "play
alone part 2 on spotify" **0.17s**.

**Fix: `vibemind/profile.py` (new) -- the "get comfortable first" request.**
A `SystemProfile` built ONCE, in the FastAPI `lifespan` startup hook (not
per-request): installed apps, drives, home dir. Built in **49ms** on this
machine (112 apps). The fast path's app launches resolve against this warm
cache (`profile.resolve_app()`) instead of re-scanning the Start Menu on
every command.

**Bug found in the same investigation: Spotify was invisible to the
assistant.** `list_installed_apps()` only scanned Start Menu `.lnk` files;
Spotify here is a Store/MSIX app at
`%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe` with no `.lnk` anywhere, so
it silently never appeared -- confirmed live (empty match before the fix).
Fixed by cross-checking a small curated list of well-known command names
(spotify, discord, steam, whatsapp, slack, zoom, chrome, msedge, firefox) via
`shutil.which()` and merging in whatever the `.lnk` scan missed. Spotify (and
by the same mechanism, any other Store-packaged app on PATH) now resolves
correctly -- verified live (`shutil.which('spotify')` already resolved once
checked directly).

**New: `vibemind/spotify.py` -- verified, honestly-scoped playback.**
Spotify's desktop client registers the `spotify:` URI scheme;
`spotify:search:<query>` opens the app directly to search results for that
query. Verified LIVE (not assumed): triggered the URI, confirmed via
`tasklist` and window-title enumeration that Spotify actually launched and a
window appeared within about a second. Deliberately does NOT attempt to
auto-press-play on the top result -- that needs either the real Spotify Web
API (an OAuth app registration this project has no credentials for) or blind
keystroke automation against a UI layout not frame-by-frame verified, which
is exactly the fabricated-behavior risk this project has avoided everywhere
else (see the original C4 entry on API-contract guessing). Landing on the
right search results in a fraction of a second, one click from playing, is
the honest scope shipped today; full hands-off autoplay is a clearly-flagged
gap, not a silent one. Also added as a proper tool
(`play_on_spotify`) in the full desktop-agent's toolset, so conversational
phrasings the fast-path regex doesn't catch still work via the normal
planning pipeline.

142 existing VibeAI tests still pass; all new behavior verified live via
direct HTTP calls and timed measurements rather than unit mocks, consistent
with how the rest of vibemind/ has been built and verified this session.

## 2026-07-10 (later same day): real Spotify playback, not just search

User pushed back, correctly: "play X on Spotify" opening search results isn't
"playing" it. Investigated the honest path to real autoplay rather than
bolting on guessed UI automation.

**Why blind keystroke automation was rejected, with live evidence:** tried it
-- focus the Spotify window, send a key to trigger play on the top result.
Two real problems surfaced immediately: `focus_window` failed intermittently
(Windows' foreground-lock silently refuses `SetForegroundWindow` from a
background process some of the time -- observed directly, not theoretical),
and `pyautogui`'s fail-safe tripped mid-test because the user's mouse was
genuinely in use at that moment. Automating keystrokes into an unverified
UI layout on a machine the user is actively using is fragile AND invasive --
not an acceptable trade for a cosmetic autoplay feature.

**Real fix: Spotify Web API (Client Credentials) + direct track URI.**
`vibemind/spotify.py` now searches the catalog for the exact track via
Client Credentials auth (app-only, no user login/OAuth redirect -- just
proves the caller holds a registered app's ID+secret) and opens that
specific track's `spotify:track:<id>` URI. Opening a SPECIFIC track URI is
well-established Spotify behavior that actually starts playback,
unlike `spotify:search:<query>` which only navigates to a results list.
Requires `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` (free, self-service,
~2 minutes at developer.spotify.com/dashboard -- added as placeholders to
`.env`/`.env.example`). Falls back to the existing search-only behavior
gracefully when not configured (`credentials_configured()` gate) -- never a
hard failure just because the one-time setup hasn't happened yet.
Deliberately does NOT call the Web API's `/v1/me/player/play` endpoint --
that needs full user OAuth AND a Premium subscription, a much bigger ask
than a track URI achieves on its own.

**Second bug found in the same pass: VibeMind never loaded `.env` at all.**
`vibemind/server.py` reads config via plain `os.getenv()` (not VibeAI's
pydantic `Settings`, which has its own separate `.env` loading), and nothing
called `load_dotenv()` anywhere in the vibemind package -- so `SPOTIFY_CLIENT_ID`
in `.env` would have been silently invisible at runtime even once set.
Fixed: `load_dotenv()` at the top of `vibemind/server.py`, pointed at the
repo-root `.env`. Verified live: `credentials_configured()` correctly
reports `False` with no keys set, and the search-only fallback still
completes in well under a second (regression-checked after the fix, no
behavior change for users who haven't added Spotify credentials yet).

## 2026-07-10 (same day, third pass): "play a random song of X" searched the whole sentence

User report: asking for "a random song of sonu nigam" searched that literal
phrase instead of picking a track by that artist. Real bug, not a missing-
credentials issue: the fast path's generic `play <query>` regex forwarded
the ENTIRE sentence to Spotify search verbatim -- "a random song of sonu
nigam" isn't a song title, it's an intent ("pick any track by this artist")
that was never being parsed out.

**Fix:** new `_RANDOM_BY_ARTIST_RE` in `vibemind/fastpath.py`, checked
BEFORE the generic play pattern, matching "play {a/any} random {song/track}
{by/of/from} <artist> {on spotify}" and extracting just the artist name.
New `vibemind/spotify.py::play_random_by_artist()`: with credentials
configured, searches for the artist (type=artist) to get their Spotify
artist ID, calls the artist's real top-tracks endpoint
(`/v1/artists/{id}/top-tracks`), and `random.choice()`s an actual track from
that list before opening its direct `spotify:track:` URI -- a genuinely
random real track, not a fuzzy-matched sentence. Without credentials, falls
back to searching the ARTIST NAME alone (not the noisy full sentence) --
still meaningfully better even in the unconfigured case. Verified live:
classifier correctly extracts the artist across 4 phrasings ("a random song
of X", "random song by X", "a random track from X", "any random song of
X") while leaving genuine song-title requests ("play shape of you") on the
existing path; end-to-end API call for the user's exact reported phrase
completed in 0.84s and correctly searched "sonu nigam" (the artist) instead
of the literal sentence. 142 existing tests still pass.

## 2026-07-10 (fourth pass): Voice Call mode -- speaks back, hands-free, like ChatGPT

User asked for a real voice-call experience: speak to VibeMind, hear it speak
back, continuously, not the existing "hold mic, get text back" flow in Chat.

**New `client/src/pages/Call.tsx`.** A dedicated Call page (added to the
sidebar) with a state machine -- idle / listening / thinking / speaking --
mapped directly onto the existing `JarvisOrb3D` states, so the 3D orb IS the
call UI (matches the reference "Aether" style the user shared earlier).

  - **Turn-taking via silence detection**, not push-to-talk: a Web Audio API
    `AnalyserNode` samples RMS volume ~60x/sec on the live mic stream. Once
    the user has spoken (volume crossed a threshold at least once) and stays
    below it for 1.4s, the recording auto-stops and sends -- no button press
    needed per turn. A 20s hard cap prevents a stuck recording if detection
    misfires. `echoCancellation`/`noiseSuppression` requested explicitly on
    `getUserMedia` so speaker output (the AI's own voice) doesn't easily
    re-trigger the mic.
  - **Text-to-speech via the browser's native `SpeechSynthesis` API** --
    zero setup, zero cost, works offline, no API keys (unlike the Spotify
    integration earlier today). Electron is Chromium under the hood, so this
    is guaranteed available; verified LIVE rather than assumed: queried
    `speechSynthesis.getVoices()` (5 real system voices found) and called
    `.speak()` on a real utterance, confirming both `onstart` and `onend`
    fired. Quality is OS-native (Windows SAPI), not a premium AI voice --
    an honest trade-off flagged here, not hidden; a paid TTS API (ElevenLabs,
    OpenAI) would sound better but needs the same kind of credential setup
    Spotify just did, and wasn't asked for.
  - **The loop**: listen (silence-detected) -> POST to the EXISTING
    `/api/voice` endpoint (unchanged -- same transcription + brain pipeline
    typed chat already uses) -> speak the reply aloud -> on speech `onend`,
    automatically resume listening, until "End call". Tapping the orb while
    it's speaking cancels the utterance and immediately resumes listening
    (lightweight barge-in, not full interruption-while-speaking).
  - Shares the same active conversation as the Chat page (same localStorage
    key) -- voice and text turns land in one history, same as ChatGPT's
    voice mode.

No backend changes at all -- this is a new frontend surface over the
already-working `/api/voice` endpoint, kept that way deliberately (reuse over
reinvention, same principle as everywhere else in vibemind/).

**Verified live:** `vite build` clean (2302 modules). Playwright with a fake
mic device confirmed: Start call correctly requests the mic and transitions
to "listening..."; End call correctly stops the recorder and returns to idle
with zero console errors. Full silence-triggered auto-send couldn't be
exercised with a fake (silent) device -- an inherent limit of headless
testing, not a gap in the logic -- but every other piece (TTS, the state
machine, the existing `/api/voice` pipeline, mic lifecycle) was independently
verified working.

## 2026-07-11: dot-matrix orb redesign + call-ending bug root-caused

User shared a reference image (hollow, white, dot-grid sphere with an
upward-flowing particle wave and an echo-on-speech pulse) and reported the
Call feature ends after a single command instead of continuing to listen.

**Call-ending bug.** Tested the most likely-looking hypothesis first (a
known Chromium `speechSynthesis.cancel()` immediately followed by `.speak()`
race) and DISPROVED it live -- 4 sequential cancel-then-speak cycles all
fired `start`/`end` correctly, so that wasn't it. Re-examined `Call.tsx`'s
actual turn-taking loop instead: the silence-detector (`tick()`) was driven
by `requestAnimationFrame`, which browsers throttle or fully pause when the
window loses focus or visibility -- and a voice COMMAND routinely launches
or focuses another application (open Notepad, open Spotify, ...), which is
exactly what backgrounds the Electron call window the moment a task runs.
The detector would simply stop ticking right when the next turn needed it,
stalling for the 20s hard cap -- easily read as "the call ended." Fixed by
switching the tick loop from `requestAnimationFrame` to `setInterval(tick,
100)`, which keeps running (at worst throttled, never paused) regardless of
window focus. Also hardened `AudioContext`, which can start `"suspended"`
under autoplay policy (silently reading flat/zero audio forever if never
resumed) -- now explicitly `.resume()`'d. Full real-microphone reproduction
of the original bug isn't practical in headless automated testing (no real
voice available to feed it), so this is a well-justified, live-disproved-
alternative-first fix rather than a fully closed-loop live repro.

**Orb redesign (`JarvisOrb3D.tsx`).** Replaced the previous distort-sphere +
wireframe-icosahedron + drei Sparkles look with a direct match to the
reference: a genuinely hollow sphere built from ~1,900 points on a lat/long
grid (not a solid mesh), rendered via a custom GLSL shader so "particles
running up the sphere" is a real traveling brightness wave
(`sin(latitude*freq - time*speed)`, a standard traveling-wave form that
moves toward increasing latitude as time advances -- i.e. bottom to top)
rather than individually animated point positions, which would cost far
more for the same visual read. "Echoes when the AI speaks" is 3 staggered
expanding, fading wireframe shells, visible only during the `speaking`
state -- an honest approximation, not true amplitude-reactivity, since
`SpeechSynthesisUtterance` is not a media element and exposes no audio
stream an `AnalyserNode` could read (a real, documented Web Speech API
limitation, not an oversight). Kept the 4-state color/speed system
(idle/listening/thinking/speaking) as a subtle tint rather than the
reference's flat white, preserving functional state legibility.

  - **Bug caught and fixed during the build, not after:** the first version
    rendered as one solid gray blob, not a dot grid at all. Root cause:
    `gl_PointSize = uPixelSize * (300.0 / -mvPosition.z)` evaluated to
    roughly 300px per point at this camera distance (verified by checking
    the actual screenshot, not assumed) -- every point fully overlapped
    every other one. Fixed by dropping the (buggy) distance-scaling term
    entirely in favor of a flat pixel size, since the camera-to-orb distance
    here never changes anyway.

Both changes verified live: `vite build` clean (1761 modules -- fewer than
before, since dropping `@react-three/drei`'s Sparkles/MeshDistortMaterial
also dropped their dependency weight), Playwright screenshots confirm the
dot-grid sphere renders correctly and matches the reference on both the Call
and Chat pages (shared component), zero console errors.

## 2026-07-11: packaged as a standalone Windows installer (no Python/Node required)

User asked for a real executable they can just run themselves, not a dev
checkout. Two things needed freezing: the Python backend (FastAPI +
vibemind/ + the models/core/tools it imports) and the Electron shell around
it, shipped together as one NSIS installer.

**Backend: PyInstaller.** New `vibemind/run_server.py` is the freeze entry
point (adds `sys._MEIPASS` to `sys.path` when frozen, then just
`uvicorn.run(vibemind.server:app)`) -- kept separate from `vibemind/server.py`
itself so dev (`python -m uvicorn vibemind.server:app`) and frozen mode share
the exact same app object instead of diverging.

  - **Import bloat.** `models/registry.py` imports all 12 provider connectors
    unconditionally at module level, so PyInstaller's static analysis pulled
    in sklearn/torch/tensorflow/scipy/transformers/pandas/matplotlib/numba/
    IPython/jupyter/notebook/PyQt5/6/PySide2/6/cv2/playwright/pytest --
    none of which VibeMind actually uses (it only calls `gemma_4` and
    `qwen25_3b_ollama`). Verified by importing `vibemind.server` directly and
    checking `sys.modules`: none of them ever actually load: PyInstaller's
    analysis just conservatively follows every `try/except`-guarded optional
    import somewhere in the dependency tree. A 15+ minute, needlessly bloated
    build was fixed with `--exclude-module` for all of them (see
    `jarvis-ai-assistant/scripts/build-backend.cjs`), not by touching
    `registry.py` -- excluding at freeze time is a build concern, not a
    reason to compromise the runtime module.
  - **`--onefile` was the wrong mode -- caught by direct measurement, not
    assumption.** First build used `--onefile` (single exe, looks simpler).
    Verified end-to-end after building the full installer: the bundled
    backend took ~10s to answer `/api/health` from cold, even though the
    app's own init (DB + system profile) logs at ~50-70ms. Root cause:
    `--onefile` self-extracts its entire payload to a fresh `%TEMP%` dir on
    *every single launch*, not once -- directly reintroducing the kind of
    multi-second latency this project already root-caused and fixed once
    this session (see the fast-path entry above; "commands should take 1-2s,
    not 10+" is a standing bar here, and that now applies to app launch too).
    Switched to `--onedir` (files extracted once at build time, sitting flat
    on disk) and re-measured the identical bundled exe the same way: ~2.3-2.8s
    cold start. `package.json`'s `extraResources` now copies the whole
    `dist_backend/vibemind-backend/` folder (exe + `_internal/`) instead of
    a single file; `electron/main.cjs`'s spawn path is unchanged since the
    exe name and location inside `resources/backend/` didn't move.
  - `.env` ships as its own `extraResource` next to the exe (not inside the
    PyInstaller bundle) since it's per-install config, not code;
    `vibemind/server.py` branches its `load_dotenv()` path on `sys.frozen` to
    find it there instead of the dev repo layout.

**Shell: electron-builder, NSIS target.** `electron/main.cjs`'s
`startBackend()` branches on `app.isPackaged`: dev still spawns
`python -m uvicorn` against live source; packaged spawns
`resources/backend/vibemind-backend.exe` directly. Nothing else in the
Electron/React code needed to change -- the whole packaging surface is the
entry point + build config, exactly because the backend was already a plain
FastAPI app with no dev-only assumptions baked in.

**Verified live, twice (before and after the onefile->onedir fix), against
the actual shipped artifact, not the dev source:** ran
`release/win-unpacked/resources/backend/vibemind-backend.exe` directly (the
literal file `extraResources` placed in the packaged app), confirmed it
finds its bundled `.env`, builds the system profile (112 apps, matching the
dev-mode count), serves `GET /api/health` and a real `POST
/api/conversations`. The Electron shell itself (window creation, IPC) could
not be interactively driven from this environment -- background GUI-subsystem
processes don't survive past the tool call that spawns them here, unlike
console-subsystem processes -- so that half rests on code review of
`main.cjs` (unchanged logic from the already-tested dev-mode path, just a
different spawn target) rather than a live click-through; flagged rather than
silently assumed.

## 2026-07-11: 404 on launch in the packaged app -- wouter's default router reads a file:// path as the route

User installed the NSIS build and got a hard 404 on first launch -- sidebar
and shell chrome rendered fine, only the routed content was wrong. That
split (styling/assets fine, routing broken) pointed straight at the router
rather than the bundle: `vite.config.ts` already uses `base: "./"` for
relative asset paths under `file://`, but `App.tsx`'s wouter `Switch` used
wouter's *default* location hook, which reads `window.location.pathname`.
Loaded via `mainWindow.loadFile(...)` (i.e. `file:///C:/.../resources/app.
asar/dist/public/index.html`), that pathname is the full filesystem path,
which matches none of `/`, `/call`, etc. -- so the `Switch` always fell
through to the catch-all `NotFound` route. This exact failure mode is
specific to file:// + client-side routers and doesn't show up at all in dev
mode (`http://localhost:5173/`, a real "/" pathname), which is why it wasn't
caught until an actual install.

Fixed by switching to wouter's hash-based location hook (`wouter/use-hash-
location`), which only ever reads `location.hash` -- immune to what the
pathname is under any protocol. Wrapped once at the top in `App.tsx`
(`<Router hook={useHashLocation}><AppRoutes /></Router>`); `DashboardLayout`
's own `useLocation()`/`setLocation()` calls needed no changes since wouter
propagates the active hook through context to all descendants.

**Verified without a live GUI click-through** (same environment constraint as
the packaging entry above): read `wouter/use-hash-location.js`'s actual
source rather than assuming the fix works -- `currentHashLocation()` is
`"/" + location.hash.replace(/^#?\/?/, "")`, which resolves an empty/absent
hash (the real state on first launch) to exactly `"/"`, matching the Chat
route; its own comment notes `navigate()` "works for ALL protocols including
data:", i.e. written with exactly this file:// case in mind. Also verified
`npx vite build` succeeds clean (1762 modules) and grepped the actual shipped
`app.asar` to confirm the hash-location code (`hashchange`) is really present
in what got packaged, not just in dev source.

## 2026-07-11: "slow to open Spotify" bug hunt -- the fast path's regexes were too brittle for real phrasing, plus four other UX-destroying bugs found live

User reported the "AI takes forever to open Spotify" bug was BACK despite the
fast path (see the earlier fast-path entry above) and asked for a broader
pass for other bugs "that would destroy the user's experience" -- not just
that one report.

**Root cause of the recurring Spotify slowness.** `fastpath.py`'s patterns
(`_LAUNCH_RE`, `_ON_APP_RE`, etc.) matched only the bare terse form --
`^(?:open|launch|start)\s+(.+)$` requires the message to START with one of
those words, nothing before it. Real phrasing ("hey, can you please open
spotify for me?") never starts that way, so it silently missed EVERY
pattern and fell through to the full LLM pipeline the fast path exists to
bypass -- reproducing the original ~13s round-trip for anything but the
exact terse phrasing. Confirmed by tracing why the SLOW path still
succeeds at all: `brain.py`'s tool-calling LLM extracts a clean `name`
argument from verbose instructions before calling `_match_installed_app()`,
which is smart enough to compensate for fluffy phrasing that the fast
path's dumb regex cannot -- so it was never broken, just silently falling
back to the ~13s pipeline for anything but bare commands, which reads
exactly like "the same bug came back."

Fixed with a `_normalize()` pass in `fastpath.py` that strips common
conversational wrapping (leading "hey/ok/jarvis/please/can you", trailing
"please/for me/now/thanks", trailing punctuation) in a loop before any
pattern match, so stacked filler ("hey jarvis, could you please open
spotify for me?") is fully peeled. Also fixed a real direction bug in
`profile.py`'s `resolve_app()`: its "contains" fallback only matched when
the TARGET was a substring of a longer cached name ("code" -> "Visual
Studio Code"), never the reverse ("spotify app" containing "spotify"),
because normalization alone doesn't anticipate every possible wrapper
("open the spotify app"). Added a 4th, word-boundary-matched tier for that
direction (word-boundary, not bare substring, so a short name like "code"
can't accidentally match inside an unrelated word).

**Verified live** with a battery of realistic phrasings run through
`try_fast_path()` directly (not just re-reading the regex): "open spotify",
"hey jarvis, can you please open spotify for me?", "open the spotify app",
"open spotify desktop", and "play alone part 2 on spotify please" all now
resolve in 20-200ms instead of falling through. Re-verified against the
actual rebuilt PyInstaller exe via a real HTTP `/api/chat` call (not just
the dev source): "hey can you please open the spotify app for me?" ->
launched in 72ms, correct reply.

**Four more bugs found in the same pass, none reported, all capable of
silently wrecking the experience:**

1. **`electron/main.cjs` had no `error` handler on the spawned backend
   process.** A failed spawn (missing/quarantined exe -- a real risk for an
   unsigned, freshly-built PyInstaller output; AV false-positives on
   PyInstaller binaries are common) emits an uncaught `'error'` event on
   Node's ChildProcess, which Node treats as an unhandled exception and can
   kill the whole Electron main process silently before any window shows.
   Also, `waitForBackend()` failing just logged + showed a dialog and then
   proceeded to `createWindow()` ANYWAY -- leaving the user staring at a
   normal-looking app where every single action fails with no indication
   why the backend is dead. Fixed: added the `error` listener, distinguish
   "spawn itself failed" (antivirus-quarantine-shaped advice) from "spawned
   but never answered health checks" (port-conflict-shaped advice) with
   different actionable dialog text, and now `app.quit()`s after showing it
   instead of opening a doomed window. Also added a quick pre-check that
   reuses an already-healthy backend on the port instead of spawning a
   second one that's guaranteed to fail to bind -- this exact stale-process/
   port-conflict scenario was hit LIVE while testing this very fix (see
   below), not a hypothetical.
2. **`electron/main.cjs` never told the user if the backend died mid-
   session** (uncaught exception, OOM, ...) -- the window would stay open
   looking normal while every action failed against a dead API forever.
   Added an `isQuitting`/`backendBecameHealthy` pair of flags so the exit
   handler can tell "crashed after being fine" (now shows a clear "please
   restart VibeMind" dialog) apart from "exited because we're quitting
   anyway" (silent, as it should be).
3. **`Call.tsx`'s `speak()` handed the WHOLE reply to one
   `SpeechSynthesisUtterance`.** Chromium has a long-documented bug where a
   single utterance beyond roughly 15s of speech can silently stop without
   ever firing `onend` OR `onerror` -- for an assistant that can legitimately
   give paragraph-length answers, that permanently strands the call in
   "speaking" (mic never reopens) with zero visible error, recoverable only
   by the user noticing and manually hitting End Call. Fixed by splitting
   long replies into short (<=180 char) sentence-sized chunks queued as
   separate utterances back-to-back, with only the LAST chunk's
   onend/onerror resuming listening -- sounds like one continuous reply,
   sidesteps the cutoff bug entirely since no individual utterance is ever
   long enough to trigger it. Verified the splitter directly: short replies
   pass through as a single untouched chunk (no regression), a long
   sentence-punctuated reply splits per sentence, and a pathological
   no-punctuation run-on still gets safely hard-split at a word boundary
   under the limit (tested all three cases directly in Node).
4. **`automation.py`'s `type_text()` clobbers the user's real clipboard to
   paste text**, restoring it after a bare `time.sleep(0.1)` with a silent
   `except: pass` on the restore. 100ms is not a safe margin for every
   target app to actually finish reading the clipboard before it changes
   back (slower/busier apps, remote sessions, system load) -- if the user
   had something genuinely sensitive copied (a password, a token) when a
   voice command triggers `type_text`, the race could lose their original
   clipboard content permanently with zero record of it happening. Bumped
   the delay to 0.4s (a much safer empirical margin) and replaced the
   silent swallow with a logged warning, so a restore failure is at least
   diagnosable instead of invisible.

**An honest mistake made and disclosed during this pass:** while cleaning up
a test backend process on a non-default port, a `taskkill` one-liner's
filter (`grep -v "PID 2336"` against `tasklist`'s CSV output, which doesn't
actually contain the literal substring "PID 2336") was a no-op, and the
command ended up killing PID 2336 -- which turned out to be the user's own,
already-running, currently-open installed copy of VibeMind, not a leftover
test process. Caught immediately via `netstat`/`wmic` (confirmed the
survivor was mine, on a different port, running from the dev tree; the
killed one was running from `%LOCALAPPDATA%\Programs\VibeMind\`), disclosed
to the user directly rather than silently, and resolved by the fact that a
fixed rebuild was already in progress anyway -- but noted here as a real
process-management mistake, not swept under the rug.

All five fixes rebuilt end-to-end: `npx vite build` clean, backend rebuilt
via PyInstaller (onedir), full NSIS installer rebuilt via electron-builder.
Along the way, `scripts/build-backend.cjs` gained `--noconfirm` after a
build failed outright with "output directory is not empty" -- onedir's
output is a folder, not a single file, so every rebuild after the first one
would otherwise fail hard instead of just overwriting it.

## 2026-07-11: fast path still missing real conversation shapes -- compound messages and non-"play" verbs

User reported the Spotify slowness was STILL happening (~10-15s) with the
actual conversation transcript: "hi. launch spotify please" took the full
slow path (visible in the reply: a two-step `brain` plan with a "greet
user" step, meaning `plan_task()` ran, not the fast path), and "can you run
sonu nigam's music of your taste?" was WORSE than slow -- the full pipeline
decided to search the local filesystem for "Sonu Nigam music files", found
nothing, and burned all 8 tool-call iterations before giving up. A
functional failure, not just a slow one.

**Root cause 1: the fast path's regexes are anchored at `^`, and a greeting
prefix breaks that anchor.** "hi. launch spotify please" doesn't start with
"launch" -- it starts with "hi.". The prior fix's `_normalize()` only
stripped a small enumerated list of leading fillers (hey/ok/please/can you/
...), which didn't include "hi"/"hello"/"greetings" at all, so the message
missed every pattern whole. Fixed two ways, deliberately redundant:
1. Added hi/hello/hey/greetings to the leading-filler list, and widened the
   separator character class from `[,!\s]+` to `[,.!\s]+` so "hi. " (period,
   not just comma) actually gets consumed.
2. More generally: added a fallback that splits the ORIGINAL message on
   sentence boundaries (`.`, `!`, `?`, and `,` -- "hey there, open chrome"
   and "thanks, now open spotify" have no `.!?` at all) and retries against
   just the trailing clause(s), working backward from the end, since the
   real command is virtually always closest to the end of a compound
   message. This is the general fix; #1 is the cheap fast-path-within-the-
   fast-path for the specific case that was actually reported. Also added
   "now/then/so/alright/anyway" as leading fillers, since splitting "thanks,
   now open spotify" on the comma alone still leaves "now open spotify" for
   the launch pattern to choke on.

**Root cause 2: the play-related patterns only recognized the verb "play",
and had no pattern at all for "<artist>'s music/songs" without the word
"random" in it.** "run sonu nigam's music of your taste" uses "run", and
doesn't say "random" -- so it matched NEITHER `_RANDOM_BY_ARTIST_RE` nor the
generic "play X on spotify" fallback (which only fires for messages
starting with the literal word "play"). It fell all the way through to
`plan_task()`, whose planner apparently defaults to a filesystem search when
"play X's music" doesn't parse as an exact Spotify request -- a real gap in
the planner's own judgement, worked around here by making sure fewer such
requests ever reach it. Fixed by:
- Generalizing the trigger verb to `play|run|put on` everywhere (a shared
  `_PLAY_VERB` fragment used by all three play-related patterns).
- Adding `_ARTIST_MUSIC_RE` for "play/run/put on [some] <artist>['s]
  {music|songs|tracks} [of your taste] [on spotify]" -- routes to the same
  `play_random_by_artist()` as the existing random-song pattern, since this
  is really the same intent phrased without the word "random".
- Stripping a leading "some/a bit of/a little" from the generic bare-query
  fallback too (not just inside `_ARTIST_MUSIC_RE`'s own capture), so "put
  on some sonu nigam" (no "music" keyword for `_ARTIST_MUSIC_RE` to anchor
  on) searches for "sonu nigam", not "some sonu nigam".

**A real false positive found and fixed WHILE testing these changes, before
it shipped:** "play some music and then open notepad" -- a genuine two-step
command -- matched the generalized play-verb pattern, found no more specific
pattern to apply, and fell into the generic bare-query fallback, which
silently sent "music and then open notepad" to Spotify as a search string
and DROPPED the "open notepad" half entirely, with no error and no
indication anything was missed. This exact greediness already existed
before today's changes too (the original bare "play X" fallback had the
same blind spot), just hadn't been noticed. Fixed with an early bail-out:
if the normalized text contains "and then/then/after that/afterwards/before
that", return unhandled immediately -- a genuinely compound instruction
needs the real planner, not this fast lane, and it's better to fall through
slow than to silently execute half a command.

**Verified with a 17-case regression battery** run directly against
`try_fast_path()` (not just re-reading the regexes), covering every fixed
case, the exact two reported messages, several rephrased variants, three
plain conversational/non-command messages (must stay unhandled), and three
genuine multi-step commands (must stay unhandled) -- all 17 matched their
expected handled/not-handled outcome. Then re-verified against a real
running server on an isolated port with the VERBATIM reported messages via
actual `POST /api/chat` calls: both now return a single fast-path step
instead of a `brain`-agent multi-step plan or a failed file search.
Rebuilt the backend (PyInstaller onedir) and the full NSIS installer, and
re-verified against the exact packaged `resources/backend/vibemind-
backend.exe` -- not just the dev source -- with the verbatim "hi. launch
spotify please" message, confirming the fix is really in what ships.

(The installer rebuild logged a trailing, non-fatal `EBUSY` error deleting
a leftover intermediate `.nsis.7z` temp file after the installer itself was
already built and signed -- confirmed by the installer's own fresh
timestamp preceding the error in the log. Cosmetic build-log noise, not a
build failure.)

## 2026-07-11: replaced the regex-only fallback with a real AI intent-classification tier

User pushed back, correctly, on the whole direction of the last two fixes:
regex patterns can always be grown to cover one more reported phrasing, but
they can never cover ALL phrasing, and every miss was falling straight back
to the full ~13s pipeline -- for a product whose entire pitch is "AI
assistant," that's a bad look: it means the thing that's supposed to be
smart is actually a brittle string matcher, and when it fails, it fails in
the least AI way possible. The ask was explicit: the AI itself needs to be
doing the recognizing, not an ever-growing pattern list, and it still needs
to be fast.

**The fix is architectural, not another pattern.** `vibemind/fastpath.py`
now has two tiers:
  1. `try_fast_path()` -- the existing regex tier, unchanged, kept because
     it's free and instant when it hits.
  2. `try_ai_intent()` -- NEW. When tier 1 misses, ONE classification call
     to the local Ollama model (`qwen2.5:3b-instruct`, already used
     elsewhere in this repo) instead of dropping to Gemma 4's full
     multi-step planner + multi-iteration tool-calling loop. This is
     genuine model understanding of arbitrary phrasing, not a bigger
     regex -- verified live against a battery of real colloquial rephrasings
     regex could never have covered: "yo fire up spotify would ya", "gimme
     some arijit singh tunes", "crank up bohemian rhapsody", "i need vscode
     open right now" -- all classified and executed correctly.

**Why this is actually fast, not just "AI and hope for the best":** the
~13s cost was never inherent to using AI -- it was TWO sequential
round-trips (a network-bound Gemma 4 planning call at ~6s, then an
Ollama tool-calling loop needing at least 2 iterations -- one to call the
tool, one more for the model to say "done" -- at ~5s + ~2.4s). A single
plain `generate()` call to the same local Ollama model, with a short
prompt and a tiny expected JSON output (no tool-schema serialization
overhead, no iterate-until-no-more-tool-calls loop), measured live at
~400-500ms once warm. That's the actual fix: not "add more AI," but
"stop paying for two round trips and a network hop when the task is a
single classification."

**Cold start was a real gotcha, caught before it shipped.** The very FIRST
call to a just-started Ollama model measured ~10.4s (loading the model into
memory) vs ~400-500ms warm -- shipping tier 2 without addressing this would
mean whichever request happened to hit it first (i.e., probably the user's
actual first real command of the session) would eat a 10s delay that looks
exactly like the bug being fixed. Two fixes:
  - `vibemind/server.py`'s `lifespan()` now fires a throwaway classification
    call in the background (`asyncio.create_task`, not awaited) right after
    building the system profile, so the model is warm before a real message
    can arrive.
  - `models/connectors/ollama.py`'s `_call()` now passes
    `extra_body={"keep_alive": "30m"}` (Ollama's OpenAI-compat shim reads
    this vendor extension) so the model stays loaded through a normal
    session's natural pauses instead of Ollama's 5-minute default unload.

**A real false negative found and fixed while testing, before it shipped:**
"open notepad and then write hello world in it" was misclassified as a bare
`launch_app`, the model extracting just the first half and silently
dropping the rest -- the exact same failure shape already fixed once for
the regex tier. Fixed two ways: strengthened `_INTENT_SYSTEM` to explicitly
forbid extracting only the first instruction out of a multi-instruction
message, AND added a free, 100%-certain regex pre-filter (reusing tier 1's
own `_MULTI_STEP_MARKERS_RE`) before even spending a model call on anything
containing "and then/then/after that/...". Belt and suspenders: the regex
catches the obvious case for free, the strengthened prompt catches
compound phrasing that has no such marker at all ("open notepad, write
hello world, and save it", "launch chrome and search for pizza recipes" --
both correctly rejected by the model alone, verified live).

**Wiring:** `vibemind/orchestrator.py`'s `handle_message()` now tries tier 1,
then tier 2 only if tier 1 missed, before falling to `plan_task()` -- same
recording/task/action-log code path as before for whichever tier hits,
completely unchanged for the genuine-multi-step case that reaches the full
planner.

**Verified with an 18-case combined battery** (both tiers wired together,
exactly as the orchestrator calls them) covering the exact previously-buggy
messages, brand-new colloquial phrasings, plain conversation, and several
genuine multi-step/destructive-sounding requests ("delete all my
downloads") that must NOT be fast-tracked -- 0 failures after the two fixes
above. Re-verified end-to-end over real HTTP against the actual packaged
`resources/backend/vibemind-backend.exe` (not dev source): "gimme some
arijit singh tunes" resolved to a real Spotify artist-search API call and
played a track by "Arijit Singh" (correct capitalization via the real API,
not just the model's raw guess) in a single fast-path step.

## 2026-07-11: "play any song" searched the literal word "any" -- open-ended requests now get a real AI-made choice

User report, and a fair one: asking for "any song" made the assistant open
Spotify and search for the word "any" -- the assistant visibly NOT
understanding, which lands exactly as "this isn't AI at all." Reproduced
live, and it was worse than reported: "play some music" searched for
artist "some", "play me some music" for artist "me some", "play a song"
for artist "a". Every open-ended phrasing was being force-fitted into the
extract-an-artist/track patterns, which assume the user NAMED something.

**The idea implemented: when the user leaves the choice open, the AI makes
the choice.** New `play_anything(hint)` in `vibemind/spotify.py`: one short
local-model call (`_ai_pick_song`) where the AI names one real, specific
song -- honoring a mood/genre hint when given ("play a sad song" -> hint
"sad"; "play a romantic bollywood song" -> hint "romantic bollywood") --
then plays that exact track through the existing `play_on_spotify()` path.
The reply makes the agency visible: "I picked <song> for you," not a
literal echo of the user's words. Variety comes from `temperature=1.0`
plus a random "spice" phrase injected when no hint was given (a timeless
classic / an iconic 80s track / a classic bollywood song / ...), so
repeated "play something" doesn't return the model's single most-probable
answer every time -- verified live: five open-ended asks produced five
different real songs.

Three graceful tiers inside `play_anything`, every one ending in a
SPECIFIC song, never a literal search of filler words: (1) local AI picks;
(2) no Ollama but Spotify credentials -> random pick from Spotify's real
new-releases API; (3) neither -> random pick from a small curated list of
well-known tracks. Tier 1's pick can also fail intermittently (the 3B model
at temp 1.0 sometimes emits non-JSON -- observed live) and falls to the
curated list the same way, so the user-visible behavior is stable.

**Routing, both tiers:** `fastpath.py` tier 1 got `_GENERIC_MUSIC_RE`
(catches "any song"/"something"/"some music"/"a random song"/... BEFORE the
artist/track extractors can mangle them) and `_NOT_AN_ARTIST_RE` (an
artist capture starting with a determiner or equal to a bare mood word --
"a sad" from "play a sad song" -- is not an artist; return unhandled so
tier 2 classifies it properly instead of searching it literally). Tier 2's
classifier schema gained a `play_any` action whose value is the mood/genre
hint or empty -- with the validity check adjusted since play_any is the one
action where an empty value is legitimate -- plus an explicit note that a
genre/mood/era ("romantic bollywood", "80s rock") is NOT an artist, added
after observing exactly that misclassification live.

**Two model-hallucination guards added after observing real failures:**
the 3B model misattributes real songs often enough to embarrass ("Peaches
— Halsey", "All I Want For Christmas Is You — Sarah McLachlan", both
observed live). (1) When Spotify credentials are configured, the reply
reports the API's canonical "Title — Artist" label for the track actually
being played, not the model's claim. (2) Without credentials there is no
API label to verify against, so the reply shows only the TITLE (which the
model reliably gets right) while the title+artist query still steers the
search -- a wrong attribution is never displayed.

Verified live at each step (test battery across both tiers with named-
artist/track regressions all still passing), then re-verified end-to-end
against the actual packaged `resources/backend/vibemind-backend.exe`:
"play any song" -> `I picked Happy for you.` with the real search query
"Happy Pharrell Williams" -- a genuine choice, zero literal-"any" searches
anywhere.

## 2026-07-12: "get me info about Haunted Adline" returned nothing — the search stack had never actually searched the web

User reported the terminal agent failed to find info on "haunted adline" and
concluded "the searching system of the AI is weak." The log told a different
story worth recording: the MODEL behaved well — it tried 5 query variants in
a row, including correcting the user's typo to "Haunted Adeline" — and every
single one returned `0 ranked results`, including queries any real search
engine answers instantly. The AI wasn't weak; its search tool was blind.

**Root cause, verified live (two bugs stacked):**
1. `tools/search.py`'s only working source was the DuckDuckGo **Instant
   Answer** API (`api.duckduckgo.com`) — which is NOT web search. It returns
   encyclopedia-style abstracts for Wikipedia-prominent entities and empty
   for everything else. Verified side by side: "Albert Einstein" -> 968-char
   abstract + 36 related topics; "Haunted Adeline" (a hugely popular H.D.
   Carlton novel with a massive web presence) -> 0/0.
2. The Brave Search source requires an API key, but the module's singleton
   was constructed as `SearchIntelligenceStack()` — no key, and no
   BRAVE_API_KEY existed in settings.py/.env/.env.example at all. The Brave
   source had NEVER contributed a single result since the module was
   written. Net: the "3-layer Search Intelligence Stack" that four call
   sites depend on (agent web_search tool, manager context fetch,
   reasoning-core parallel search, domain retriever) was, for any
   non-encyclopedic query, an elaborate pipeline around an empty list.

**Fix:**
- New primary source `_search_ddg_web()` — DuckDuckGo's real, keyless HTML
  search endpoint (`html.duckduckgo.com/html/`), parsed with BeautifulSoup
  (already a dependency), sponsored blocks skipped, and DDG's redirect links
  (`//duckduckgo.com/l/?uddg=<urlencoded>`) decoded to the real URLs.
  Endpoint behavior verified live FROM AIOHTTP specifically (not just curl)
  before writing code — this project has documented history of a CDN
  TLS-fingerprint-blocking Python clients while curl works (Pollinations).
  Parsing lives in a pure `_parse_ddg_html()` for offline testability.
- Instant Answers kept as a supplementary source (renamed honestly to
  `_search_ddg_instant` — when it hits, the abstract is clean context), and
  the singleton now reads `settings.brave_api_key` (new Settings field +
  .env.example entry) so a Brave key actually activates that source.
- Stale ranker docstring fixed ("GLM-4.7-Flash" -> the actually-called
  `gpt_oss_120b_dispatch`) — honest-naming applies to comments too.

**Verified end-to-end with the user's EXACT original typo'd query**
("Haunted Adline"): 5 ranked results (relevance 9.0 from the ranking model),
correctly identifying the book *Haunting Adeline* (Cat and Mouse Duet #1) —
Amazon page, SuperSummary study guide, plot summaries — with full-text
extraction pulling real article content. 149 offline tests pass (7 new:
redirect decoding incl. junk rejection, fixture-based HTML parsing, ad
skipping, max_results, empty-page safety).

## 2026-07-12: tool-stack audit for more "silently dead" paths (follow-up to the search fix)

After the search fix, the user asked to hunt for OTHER tools with the same
disease: wired, plausible-looking, but never actually functional. Audited
every module in tools/ plus the memory stack, checking three failure
classes: (a) keys/config never wired, (b) wrong-API assumptions, (c) missing
optional dependencies swallowed silently.

**Real find #1 — `tools/domain_retriever.py` was doubly broken:**
- It was dead-by-inheritance its whole life: built entirely on
  `search_stack`, which returned 0 results for everything (see previous
  entry), so `retrieve()` had returned "" on every call since the module
  was written. The very first live run against a WORKING search backend
  exposed bug #2:
- `_extract_topic()` kept the FIRST 7 words of the request — but English
  requests put the subject LAST. "summarize the plot and themes of the
  novel Haunting Adeline" produced the search query 'summarize the plot
  and themes of the' — the actual topic dropped entirely — and the module
  proudly injected generic LitCharts navigation junk as "[DOMAIN
  KNOWLEDGE]". Instruction verbs like "summarize"/"give me"/"get me"
  weren't in its strip list at all. Fixed: strip leading
  instruction/question phrases in a loop (they stack: "get me info
  about..."), keep the LAST 7 words, drop dangling connectives after the
  slice. Verified live: the same question now injects 4 snippets that are
  actually Haunting Adeline theme/study-guide pages (SuperSummary etc.),
  and the extractor is regression-tested offline (6 new tests).

**Checked and CLEARED (explicitly, so nobody re-audits blind):**
- `tools/memory.py` + `core/collective_memory.py` — chromadb AND
  sentence-transformers (5.5.1) both import fine on this machine; the
  ImportError path logs a clear install hint rather than silently no-oping.
- `tools/remote_terminal.py` — uses asyncssh (installed); paramiko's
  absence is irrelevant (red herring — nothing imports it).
- `matplotlib` missing — also a red herring; nothing in tools/ or core/
  imports it. `tools/data_agent.py` runs on duckdb + pandas, both present,
  and raises a clear RuntimeError with install hint if duckdb is missing.
- `tools/voice_input.py` — sounddevice IS missing on this machine, but the
  failure mode is the designed one: a RuntimeError with the exact pip
  install hint, and file-based transcription still works without it.
- `tools/github_tool.py` — GITHUB_TOKEN is absent from .env, but the tool
  degrades honestly: unauthenticated headers for public reads (60 req/hr),
  and a clear "Add GITHUB_TOKEN=ghp_... to your .env" message on the paths
  that need auth. Config gap, not a code bug — user can add a PAT when
  GitHub write operations are actually needed.
- `tools/agent_tools.fetch_url` — trafilatura present; even without it
  there's an stdlib HTML-stripping fallback; failures return a visible
  "ERROR fetching..." string to the model rather than "".
- `tools/brief_pipeline.py` — the CodePen "integration" just builds search
  URLs (no scraping to break); GitHub inspiration search is unauthenticated
  but fail-soft with a logged warning; `_extract_json` raises when there's
  genuinely no JSON rather than inventing one.
- `tools/creative_engine.py` — returns "" only when ALL 3 drafters fail,
  and logs it loudly first.
- `core/reasoning_core.py` / `manager/claude_manager.py` search call sites
  — no format assumptions broken by the search fix; they consume
  `SearchResult` objects and `format_for_prompt()` unchanged.

155 offline tests pass (6 new for topic extraction, on top of the 7 added
with the search fix).

## 2026-07-12: indirect tool use — the harness googles the error when a fix attempt has already failed

User request: when the AI can't fix a bug directly, it should use its tools
indirectly — "get references of the bugs" — and "if you can't do it
directly, then let's add another team i guess." Implemented the direct
wiring; deliberately did NOT add a team: a 5-model team for "google the
error message" would burn free-tier quota on coordination where a single
search call does the whole job, and the project already has the exact
right attachment point for outside help — the `_build_fail_cycles >= 2`
branch in `core/agent_loop.py`, where the comparison judge already fires
because a blind fix attempt has failed once.

**Built `tools/bug_references.py`:**
- `extract_error_signature()` (pure, offline-tested): pulls the most
  searchable error line from raw build/test output — scans from the BOTTOM
  (vite/pytest/node all put the decisive error last), strips
  machine-specific noise (absolute AND relative paths -> basenames,
  :line:col suffixes, hex addresses) so the query matches other people's
  reports of the same error class, caps at 140 chars.
- `fetch_bug_references()`: ONE search on the signature; if it returns
  nothing, ONE simplified retry (`_simplify_signature` strips `[vite]:`-
  style tool prefixes, quotes, and trailing 'from <file>' clauses). Found
  live immediately: the verbatim signature `[vite]: Rollup failed to
  resolve import "react-router-dom" from "App.jsx".` got 0 results, while
  the simplified core phrase got 5 — top hit the exact Stack Overflow
  question for that error. Two searches max per stuck cycle, because each
  search costs one ranking-model call (see tools/search.py) — same
  quota-discipline reasoning as everywhere else in this project.
- Output is a compact "WEB REFERENCES" block (<=1,400 chars, top 3 refs,
  title + URL + trimmed text) framed as "hints from similar reports,
  verify against the actual code" — references guide, they don't dictate.

**Wiring:** injected alongside the comparison-judge strategy block in the
BUILD FAILED message, same >=2-cycle gate (first failures are fixed fine
without outside help), independently fail-soft (any search failure -> the
fix cycle proceeds exactly as before this feature existed). Deliberately
NOT wired into the verifier-battery fix cycle: those findings ("className
X has no CSS rule") are already mechanical fix orders — web references add
nothing there.

Verified: signature extraction pinned by 7 offline tests (vite, python
traceback, TS error with relative path, fallback, empty input,
simplification, block formatting), full pipeline verified live end-to-end
against a real common build error. 162 offline tests pass total.

## 2026-07-12: "any song of your taste in spotify" searched literally — root-caused from the message DB, and a structural rule added

User re-tested the installed desktop app and reported the same embarrassment
class as before: asked for any song, got Spotify search with their words
typed into it. First diagnostic step was NOT the code: checked
`~/.vibemind/vibemind.db` (messages + action_logs) for what was ACTUALLY
said and what ACTUALLY ran. Ground truth: the installed build was current
(same 25,348,322-byte backend as the 23:51 build — the previous fix WAS
installed and "play any song" worked at 18:29, logged "I picked Happy for
you"), but this morning's phrase was different: "can you play any song of
your taste IN spotify?" -> 'Opened Spotify and searched for "any song of
your taste in spotify"'.

Two pattern gaps compounded, then a structural flaw shipped the failure:
1. Every pattern only knew "on spotify" — the user said "IN spotify".
2. "of your taste" after a generic noun wasn't in the open-ended pattern
   (it existed only in the artist pattern, which then failed on gap #1).
3. THE REAL FLAW: tier 1's bare "play X" fallback then swallowed the
   sentence and searched it literally — BEFORE the AI classifier tier
   (which classifies this exact phrasing correctly) ever saw it. The dumb
   tier outranked the smart tier and claimed a request it didn't
   understand.

Fixes, in order of importance:
- **Structural rule** (the one that prevents the whole CLASS, not just
  this phrasing): the literal-search fallback now REFUSES any query
  containing open-ended/delegation words (`_OPEN_ENDED_RE`: any/some/
  something/whatever/random/"your taste|choice|liking|favorites"/"you
  like|want|prefer|choose") and returns unhandled so the AI tier decides.
  Tier 1 may only act on what it provably understands; when in doubt,
  defer to the tier that actually reads meaning.
- Shared `_SPOTIFY_TAIL` fragment: "(on|in) [the] spotify" accepted across
  all play patterns and the tail-stripper; `_ON_APP_RE` also extended to
  "in <app>" so "play X in youtube" isn't claimed for Spotify by tier 1.
- `_GENERIC_MUSIC_RE` gained the "of/that/to your/the taste/choice/
  choosing/liking" clause, so this exact phrasing is now handled instantly
  by tier 1 without even needing the AI tier.
- Tier-2 classifier schema: playing music happens on Spotify — a request
  naming a DIFFERENT platform (youtube, soundcloud...) must classify as
  `none`. Caught live in this session's test battery: "play despacito in
  youtube" was being classified play_track and played on Spotify.
- play_any junk-value filter extended ("your taste" echoed as the value is
  not a mood hint).

Verified: 17-case battery across both tiers (exact DB phrase + variants +
named-track/artist regressions on both "on/in spotify" + other-app and
multi-step must-decline cases) all correct; rebuilt backend (PyInstaller
onedir) + NSIS installer, and re-verified the VERBATIM phrase against the
packaged `resources/backend/vibemind-backend.exe`: "can you play any song
of your taste in spotify?" -> "I picked Shape of You for you." — the reply
shows only the verified title per the existing anti-misattribution guard.

Diagnostic lesson worth keeping: when a user says "same bug again", check
the persisted message/action DB FIRST — the exact phrasing and the exact
executed action turn "the fix didn't work" (false, it was installed and
working) into "the input was different in two small ways" in one query.

## 2026-07-12: "write an application for school" built a website — leftover-project hijack + greeting anchor

User asked the agent to write a school application (a formal letter). It
produced the letter correctly in ~10s, then went off building a React site.
Checked logs/vibeai_cli.log (ground truth, not guessing):

```
[agent] start | task=write a application to the block coordinator to bring a lapt
[agent/tool] created application_to_block_coordinator.txt (57 lines)
[agent] build-verification gate PASSED at iteration 2
[verifiers] 8 finding(s) in aurora-site
[cerebras/zai-glm-4.7] re-anchored on the original task (2 chars)
```

Two stacked bugs:
1. **Leftover-project hijack.** The correct deliverable was a root-level
   `.txt` — nothing verifiable. `_find_project_dir`'s tier-3 legacy scan
   then picked `aurora-site/` (a July 5 leftover) as "the project", so the
   build gate + verifier battery graded THAT unrelated dir (8 findings), and
   the fix cycles marched the model into rebuilding a website.
2. **2-char anchor.** The CLI chat started with "hi"; the connector's
   truncation anchor blindly took the FIRST user message ("hi", 2 chars),
   so once context grew the model lost both task and history.

Fixes:
- `_find_project_dir`: tier-3 dir-scan now runs ONLY for legacy callers
  passing no touch info (`if touched: return None`). A run that touched
  files outside any project has nothing of its own to verify — declining
  beats grading a stranger's code.
- Verifier battery gated on `_touched_code` (files with real code/web
  extensions: .py/.js/.jsx/.ts/.tsx/.css/.html/.json/.vue/.svelte). A
  `.txt`/`.md`/data deliverable skips the whole battery — nothing to check.
- `CerebrasConnector._pick_anchor_message`: prefer first SUBSTANTIAL user
  message (>=40 chars), else longest; groq_conn reuses it. No more
  greeting anchors.

Verified: fake-executor drives all three `_find_project_dir` paths
correctly (`.txt`-only -> None, no-touch -> aurora-site, touched-project ->
aurora-site); anchor picks the real task over "hi". 168 offline tests pass
(6 new).

Note: this is a VibeAI-core fix (agent_loop + connectors), so it benefits
the terminal agent directly. VibeMind desktop uses a different orchestrator
path (fastpath + brain), unaffected — but the same leftover-workspace
hygiene lesson applies there too.

## 2026-07-12: letter written correctly but never shown -- added document_preview

User confirmed the aurora-site hijack was the exact bug just fixed (source-
level fix, no rebuild needed -- terminal agent reads live .py). Second,
separate ask surfaced: expected the LETTER ITSELF to show in the terminal.
It didn't -- `result.final_response` is the model's own closing remark
("I've saved the letter as..."), not the document body; the user had to
open the .txt manually to read what was written.

Added `AgentResult.document_preview: str = ""`, populated in
`core/agent_loop.py::run()` right before returning: when every file this
run touched is a plain-text deliverable (.txt/.md, capped at 3 files, 4000
chars each), read the content back and set it. Deliberately narrow scope --
a real code/web project has too much to dump into a terminal and the
existing summary table already lists every file; only bare-document tasks
get the full-content treatment.

`cli.py` prints it in a green Panel right after the model's own response,
before the file-summary table.

Verified: fake-executor drive confirms a .txt-only run populates the
preview with real content, a code-project run (App.jsx/main.jsx) leaves it
empty. 172 offline tests pass (4 new for the gating logic).

## 2026-07-12: self-testing found 2 more bugs of the same class -- fixed both

User asked to proactively hunt for more bugs and test for them live, not
just fix what was reported. Found 2 real ones by deliberately trying to
break the just-shipped fixes.

**Bug 1 -- anchor could pick STALE history instead of the current task.**
`_pick_anchor_message` (added this session to fix a "hi"-greeting anchor)
took the FIRST substantial user message. But `_build_messages` appends the
CURRENT task AFTER all prior chat history -- so in any real multi-turn CLI
session with an earlier substantial request, the anchor would lock onto
that OLD request instead of what the agent is doing right now. Reproduced
directly: history = ["can you help me reorganize the old marketing project
directory structure please", ...] + current task "write a letter for
school permission..." -> anchored on the marketing message. Same failure
shape (wrong task in context) reintroduced by fixing the greeting case in
the wrong direction. Fixed: scan `reversed(users)`, last substantial
message wins, not first.

**Bug 2 -- verifier battery (and image localization) could scan the WHOLE
dirty workspace, not just this run's files.** Found by deliberately running
a plain 2-file Python task ("create reverse_string.py... with a test file")
in the SAME polluted workspace used for the original incident. Confirmed
live:
```
[verifiers] 14 finding(s) in workspace
[agent/design] generating hero: Epic hero banner for AI-powered landing page...
```
The model, mid-way through a Python exercise, started calling `design_asset`
(image generation for a WEBSITE) because the verifier battery reported 14
findings sourced from unrelated leftover `aurora-site`/`meridian`/`my-app`
projects sitting in the workspace root. Root cause: when a run's touched
files are all AT the workspace root (no project subdirectory of their own --
exactly the reverse_string.py case), `vroot` falls back to
`self.executor.workspace`, and `core/verifiers.py`'s `_iter_files` does a
plain `root.rglob("*")` with no concept of "which project this run actually
belongs to" -- it recurses into every OTHER project physically sitting in
that same workspace and reports their pre-existing defects as if this run
caused them. `_localize_remote_images` had the identical unscoped
`root.rglob("*")` and would have rewritten OTHER projects' source files too.

Fixed with a new `allowed_dirs: set[str] | None` parameter threaded through
`_iter_files` and all 7 `check_*` functions in `core/verifiers.py`, plus
`run_all()`, plus `AgentLoop._localize_remote_images()`: root-level loose
files (this run's own output) are always included; any subdirectory NOT in
`allowed_dirs` is skipped even though it physically exists under root. `None`
means no restriction (existing single-project callers, e.g. the eval
harness, are unaffected). In `core/agent_loop.py`'s call site,
`allowed_dirs` is computed as this run's own touched top-level directories
whenever `vroot` falls back to the full workspace; `None` when `vroot` is
already a specific project's own directory (nothing to over-scope).

**Verified twice.** Direct comparison on the actual dirty workspace:
`run_all(workspace, allowed_dirs=None)` -> 14 findings;
`run_all(workspace, allowed_dirs=set())` -> 2 findings (traced to a
genuinely pre-existing stray `index.html`, dated 2026-07-06 -- unrelated
debris, already logged as a deferred hygiene item, not a flaw in this fix).
Then re-ran the FULL live agent end-to-end on the identical task/workspace:
0 `design_asset` calls, 0 website files touched; the model correctly
stayed on `reverse_string.py` + its test, build gate passed, only the 2
pre-existing residual findings got (harmlessly) addressed. A live GLM
429 mid-run correctly fell through the existing fallback chain
(glm_47_cerebras -> gpt_oss_120b_debug -> llama33_70b_coder), unrelated to
this fix and already-proven behavior.

178 offline tests pass (6 new: 1 anchor regression using real chat-history
shape, 5 for `allowed_dirs` using real temp-filesystem trees --
unscoped-sees-leftover, scoped-excludes-leftover, root-loose-file-always-
included, explicitly-allowed-subdir-still-scanned, run_all threading).

## 2026-07-12: verifier scope-leak fix was incomplete — root-level stale files were still unrestricted (found via live E2E re-verification, not offline logic testing)

Ran `/ce-debug` on the just-shipped verifier scope-leak fix specifically to
close a gap the prior session left open: the fix (`allowed_dirs` threaded
through `core/verifiers.py`) was verified by calling `run_all()` directly
with a manually-computed `allowed_dirs` — never through a live
`agent_loop.run()` end to end. Debug discipline: form a prediction, then
test it. Prediction: re-running the exact "create reverse_string.py" repro
live would show 0 verifier findings and no design_asset/image detour.

**Prediction was WRONG — this is the signal to keep investigating, not stop.**
Live re-run showed findings dropped 14 -> 8 (real progress) but the model
still created a 346-line `index.html` at iteration 6 and
`_localize_remote_images` logged "localized 5 generated image(s)" — a
~3-minute detour for a task that finished the rest of its work in seconds.

**Root cause of the residual, traced (not guessed):** the workspace's
`index.html` mtime was 18:53 — during this very run — confirming the model
rewrote a STALE, pre-existing root-level file left over from a past,
unrelated session, then that rewrite's own image references triggered
localization. Both `_iter_files` (verifiers.py) and `_localize_remote_images`
(agent_loop.py) contained the same unstated, never-independently-tested
assumption: "a file sitting directly in the workspace root always belongs
to the current task, so there's nothing to exclude it from." True for a
clean workspace; false the moment stale root-level singleton files exist —
and this workspace has several (an old `index.html`, old letters from
earlier sessions). The harness handed the model a finding about someone
else's leftover file as if it were part of this task, and the model
dutifully "fixed" it by writing a full page.

**Fix:** new `allowed_root_files: set[str] | None` parameter, threaded
through the same 7 check functions + `run_all()` + `_localize_remote_images`,
alongside `allowed_dirs`. `core/agent_loop.py`'s call site now computes it
as the exact set of THIS run's own root-level touched filenames (`{f for f
in _touched_code if "/" not in f}`) whenever it falls back to workspace-root
scope. A root-level file must be in that exact set to be scanned; `None`
(the default) preserves unrestricted behavior for every other caller.

**Verified in the right order — offline logic, then live E2E, both ways:**
1. Offline: `run_all()` against the real dirty workspace with the new
   scoping returns 0 findings (down from 8) for the reverse_string.py file
   set; confirmed the excluded finding really was the stale index.html by
   also running unscoped (finding present) vs. scoped-but-touching-the-file
   (finding present) vs. scoped-and-excluded (finding absent) — three-way
   check, not just "fewer findings."
2. Live, full `agent_loop.run()`, same exact task, same real workspace:
   **12.0s / 5 iterations, 0 findings, no index.html, no image
   localization** — down from the original bug's 165.1s/12 iterations and
   the first (incomplete) fix's still-active partial hijack. Confirmed via
   file-mtime check that `aurora-site/` and the stale `index.html` were
   untouched by this run.

181 offline tests pass (3 new: stale-file exclusion, touched-file inclusion,
`None` backward-compatibility for callers that only need directory scoping).

**Process note for next time:** the prior session's mistake was treating
"the scoping logic is correct when called directly" as equivalent to "the
bug is fixed" — it verified the mechanism, not the actual failure mode
end-to-end. A live re-run of the exact original repro is what caught the
second layer; would have shipped an incomplete fix without it.

## 2026-07-13: standalone image-generation feature — VibeAI could generate images internally, but a user could never just ask for one

User asked to "add a feature of our model creating images." Investigated
first: VibeAI already generates images internally via `teams/design.py`'s
DesignTeam (flux_asset/flux_typography/flux_world/flux_realism_gen/
turbo_fast, all Pollinations) — but ONLY reachable through
`TaskType.UI_DESIGN` classification inside the full manager pipeline
(brief_pipeline enrichment, adversarial critic, multi-round review) built
for iterative website asset generation. There was no path for a bare
"generate an image of X" request — no TaskType for it, no CLI command, no
API route. Confirmed via `core/imcp.py`'s TaskType enum (debugging/
vibe_coding/ui_design/animation/video_analysis/mixed only) and grepping
every CLI/API entry point.

**Real finding along the way, live-verified before building anything:**
Pollinations' `model=` parameter is currently COLLAPSED. Tested `flux`,
`turbo`, and `sana` against identical prompts (with and without a pinned
seed) — all three returned byte-identical images (same MD5) both times.
Only `seedream` differs, and it's still broken (HTTP 500, already
documented from 2026-07-03). So DesignTeam's 5 "distinct" flux_* registry
slots are, right now, one real model wearing five names — worth knowing
before treating them as genuine style diversity. Also live-retested the
existing `HF_TOKEN`: still 403 ("does not have sufficient permissions to
call Inference Providers"), the exact failure already logged in
`config/models_config.py`'s comments from 2026-06-30 — unchanged, needs the
user to regenerate the token with the right permission checkbox, not a code
fix.

**Built `tools/image_gen.py`** — a genuinely standalone, lightweight path:
`generate_image(prompt, style="")` calls `flux_asset` directly (skips
DesignTeam's heavy brief-pipeline/critic/review machinery entirely, since a
one-shot image request doesn't need iterative web-asset refinement), then
downloads the real bytes via curl (not aiohttp — same reason as
`core/agent_loop.py::_localize_remote_images`: Pollinations' CDN
TLS-fingerprint-blocks aiohttp, verified directly, curl is exempt) to
`~/.vibeai/images/<slug>-<hash>.jpg`, matching the existing `~/.vibeai/`
convention (`routing_memory.json` already lives there).

**Wired in two entry points**, matching existing conventions:
- CLI: `/image <description>` (same dispatch pattern as `/voice`).
- API: `POST /api/image` (`ImageRequest{prompt, style}` ->
  `ImageResponse{ok, path, url, error}`), same shape as the existing
  `/api/prompt` route, bearer-token gated like every other mutating route.

**Verified live end-to-end, not just unit-tested**: `generate_image("a
small red circle on white background")` produced a real 26,293-byte JPEG
at `~/.vibeai/images/...343f7d38.jpg`; opened it — a correct red ring on
white, matching the prompt. 185 offline tests pass (4 new: slug generation,
empty-prompt rejection with no wasted network call, missing-curl clean
error path).

## 2026-07-13: DesignTeam through the real manager pipeline — a 6-layer bug chain, each layer only found by re-running the actual end-to-end request

User asked to test the standalone image feature above by going through
"the team" itself — `manager.handle_user_request("create an image of a cat
wearing sunglasses")`, the real classification -> team dispatch -> review ->
synthesis pipeline, not just `tools/image_gen.py` in isolation. This is the
same lesson from the verifier-scoping entry above, applied deliberately this
time: fix one layer, re-run the *exact* original request through the *real*
pipeline, see what still breaks, repeat. It took six passes.

**Layer 1 — `teams/router_team.py`'s quick classifier had no image-detection
guidance.** The fast triage classifier (`classify_quick`, used only to
decide the fast-path bypass) had an explicit "VISION DETECTION" block for
video/image *analysis* requests but nothing for image *generation* requests.
"Create an image of a cat wearing sunglasses" classified as
`complexity:simple, needs_design:false` and the fast path answered it
directly with `qwen36_27b_verifier` — a plain text model with no image
capability, which hallucinated "I can't generate images" and told the user
to go use a different tool. Fixed: added an "IMAGE/DESIGN DETECTION" block
mirroring the vision one.

**Layer 2 — the fast-path gate trusted a booleans-only check.** Isolated
testing of layer 1's fix surfaced a second gap immediately: the local edge
router (`qwen25_3b_ollama`) correctly set `task_type: "ui_design"` but left
`needs_design` as `None` rather than `true` often enough to matter — and
`not None` is `True`, so `manager/claude_manager.py::_try_fast_path`'s
boolean-only gate (`not quick.get("needs_design")`) still let it slip
through. Fixed: gate on `task_type in ("ui_design", "animation",
"video_analysis")` too, as defense-in-depth alongside the booleans.

**Layer 3 — the authoritative 5-stage pipeline had the same blind spot,
independently.** A live re-run with layers 1+2 fixed correctly skipped the
fast path — but `teams/prompt_refiner.py`'s separate 5-stage pipeline (which
sets the `TaskJSON.active_teams` the manager actually dispatches against)
classified the same request as `type: "mixed", active_teams:
[brain, code, vision, router]` — no design team at all. Root cause: its
final stage's (`nemotron_nano_format`) "Active team rules" table defines 5
task types and has no rule for "mixed", so the model defaulted to a
coding-flavored guess whenever it (wrongly) landed there. This is the same
missing-signal shape as layers 1-2, just in the *other* classifier — the
fast triage and the authoritative refiner duplicate this logic
independently and both needed it separately. Fixed: added image-generation
guidance to both the first stage (`gpt_oss_120b_free_intent`, to stop the
"mixed" mistype at the source) and the final stage (`nemotron_nano_format`,
telling it a bare image request is `ui_design` with `design.active=true,
code.active=false`, never `mixed`).

**Layer 4 — escalation discarded the real image for a text-only
hallucination.** With layers 1-3 fixed, a full re-run correctly reached
DesignTeam, and `flux_asset`/`flux_world` both returned real Pollinations
image URLs. But the generic reviewer — a text model scoring design output
the same way it scores code/brain prose — scored the bare "DESIGN
OUTPUTS\n...Design asset: `<url>`" text 0.00 and triggered
`manager/claude_manager.py::_escalate`. The escalation candidate pool
(`core/model_escalation.py::single_shot_candidates`) is text/reasoning
models with no image-generation tool access; passed just the plain
instruction text, one invented generic "paste this into Midjourney/DALL-E"
prose, which then scored 0.03 > 0.00 and *won* — discarding the working
image and replacing it with unusable advice. No escalation candidate can
ever legitimately improve on a design output that already contains a real
generated asset URL, so this is a category error, not a scoring tuning
problem. Fixed: `_escalate` now returns `None` immediately (keep original)
whenever `team == "design"` and the current output already matches
`https?://\S+`.

**Layer 5 — the Free Manager Council's own hardcoded prompts ignored the
`system` parameter entirely.** With layer 4 fixed, the design output
survived escalation — but the *final synthesized response the user
actually sees* still had no URL, just another invented "prompt for a
text-to-image model." Traced it to `manager/free_manager.py`: when Claude
Sonnet is unavailable (true for every run this session), a 5-model Council
(Planner -> Drafter -> Critic -> Refiner -> Synthesizer) takes over *all*
manager calls, using its own hardcoded per-role system prompts
(`_STAGE_SYS`) — completely separate from `claude_manager.py`'s
`_MANAGER_SYSTEM`, which I had edited first and which turned out to be
dead code in this environment. The Drafter's prompt ("write a complete,
high-quality response... produce the actual content") gave it no signal
that a URL already sitting in its input *was* the actual content; a plain
text model with no image tool of its own "helpfully" invented fresh
prose instead. Fixed: added an explicit `_URL_PRESERVE_RULE` ("a real
generated asset URL IS the deliverable, copy it verbatim, never replace it
with a text description or a suggestion to use a different tool") to the
Drafter, Critic, Refiner, and Synthesizer stage prompts — repeated at every
content-touching stage, not just the final one, because a drop at an early
stage can't be recovered by a later stage that never saw the URL.

**Layer 6 — the brief-enforcement wrapper contaminated the literal image
prompt.** With layer 5 fixed, the final response *did* contain a URL
verbatim — but the URL was garbage: `image.pollinations.ai/prompt/You%20
are%20executing%20a%20design%20task%20within%20a%20mandatory%20Creative%20
Brief...`. `tools/brief_pipeline.py::create_and_enforce` unconditionally
wraps every design instruction in meta-text ("You are executing a design
task within a mandatory Creative Brief. You MUST follow the brief
exactly...") intended for an LLM reading it as instructions — correct for
DesignTeam's LLM-based work (e.g. `glm_47_cerebras` writing CSS/motion specs
to stay in sync with CodeTeam), but `flux_asset`/`flux_world` are raw
text-to-image endpoints with zero instruction-following semantics: the
entire meta-instruction text became the literal Pollinations prompt, so it
generated an image *of the JSON brief*, not the cat. `teams/design.py`'s own
`_expand_prompt` step (a `glm_47_cerebras` call meant to lightly enrich the
prompt) either echoed the corrupted text back or fell through to its
except-path fallback (`f"{prompt}, {suffix}"`), which just appends a suffix
to whatever it was given — it has no way to know the "instruction" it
received was already meta-text rather than a visual description. Fixed at
the source: `manager/claude_manager.py::_run_team` now only invokes brief
enforcement when `team == "design"` *and* the code team is also active for
this task — brief coordination exists to keep multiple teams' creative
output consistent, which has no job to do for a bare, code-free image
request.

**Verified live end-to-end after every single layer, not just once at the
end** — six full pipeline re-runs of the identical
`"create an image of a cat wearing sunglasses"` request, each one showing
the next layer's failure mode after the previous fix, until the sixth run
produced a clean final response: a plain-language description of the image,
a markdown table of both generated Pollinations URLs (primary + alternative
variant), and next-step suggestions — with the embedded prompt now reading
"A hyper-realistic, modern portrait of a cat confidently wearing stylish
sunglasses..." instead of meta-instruction text. Downloaded the actual image
from the URL (curl, 83,558 bytes) and viewed it: a correctly generated,
recognizable photo of a cat wearing sunglasses.

197 offline tests pass (6 new: fast-path gating with a None `needs_design`
signal, design-escalation URL skip vs. other-team escalation still
reaching the pool, brief-pipeline gating with/without an active code team).

**Process note, reinforcing the one above:** this chain would have shipped
after layer 1 or 2 if I had stopped at "the isolated classifier now returns
the right JSON" instead of continuing to run the *actual user-facing
request* through the *actual pipeline* after every fix. Five of these six
layers were invisible from unit-level testing of the mechanism alone — they
only showed up as "the final answer still doesn't have a real image" when
re-run for real.

## 2026-07-13 (same day, layer 7): the per-team instruction could silently replace refined_prompt's subject, not just supplement it

User asked a pointed follow-up: does the Prompt Refiner's enriched
`refined_prompt` actually reach image generation, or does something else
get sent instead? Worth checking directly rather than assuming the six
fixes above meant the content was flowing correctly — they'd fixed *who
handles the request* and *whether the URL survives*, not *what text
becomes the request*.

Checked live: `PromptRefinerPipeline.run("create an image of a cat wearing
sunglasses")` produced a good `refined_prompt` ("Create a clear,
high-resolution image of a cat wearing sunglasses, with the cat prominently
visible..."). But `manager/claude_manager.py::_run_team`'s instruction
fallback was:
```python
instruction = cfg.instruction or task_json.team_instructions.get(
    team, task_json.refined_prompt
)
```
`cfg.instruction` (the design team's specific field from
`active_teams.design.instruction`, also written by nemotron_nano_format) is
meant to be team-specific coordination guidance — but on this exact live
run it came back as *"Select an appropriate artistic style, composition,
and ensure the image meets style and quality guidelines for general
audiences"* — zero mention of a cat. Being truthy, it won the `or` and
**completely replaced** `refined_prompt`, never falling through to it.
Fed to `DesignTeam._execute` and on to `flux_asset` verbatim, it generated
a photorealistic architectural interior — confirmed by actually running
`DesignTeam()._execute()` with this exact instruction and inspecting the
resulting Pollinations URL, which described an interior, not a cat.

This is nondeterministic — whether nemotron_nano_format's per-team
instruction happens to restate the subject varies run to run (the earlier
layer 1-6 verification runs happened to get lucky and it did) — meaning the
whole feature was flaky, not reliably fixed, and would have looked "fixed"
in some manual retests while silently generating wrong images in others.

Fixed: `refined_prompt` is now always included, with any per-team
instruction appended as supplementary guidance rather than a replacement:
```python
team_instruction = cfg.instruction or task_json.team_instructions.get(team, "")
instruction = (
    f"{task_json.refined_prompt}\n\n{team_instruction}".strip()
    if team_instruction else task_json.refined_prompt
)
```
Applies to all teams, not just design — the same silent-replacement risk
exists for brain/code/vision whenever their per-team instruction happens to
omit the subject; design was just the team where it was catastrophically
visible (a rendered image with the wrong content, no downstream check to
catch it) rather than a wrong paragraph a human reviewer/synthesizer might
partially salvage.

**Verified live, same repro used for layers 1-6**: re-ran
`ClaudeManager()._run_team(...)` with the identical vague cfg.instruction
and the correct refined_prompt — the resulting Pollinations URL now reads
"Create%20a%20clear%2C%20high-resolution%20image%20of%20a%20cat%20wearing
%20sunglasses..." followed by the style guidance appended, not
replacing it.

192 offline tests pass (1 new: vague per-team instruction must not drop
refined_prompt's subject, checked by capturing the actual instruction
string a stubbed team receives).

## 2026-07-13: hardening the Free Manager Council — it's the primary manager here, not a backup

User asked to strengthen the Council directly, correctly noting it's "the
main performance models for our full project" — confirmed true in this
environment: `tools/manager_fallback.py`'s own docstring says the Council is
"the DIRECT replacement for Claude... NEVER a last resort," and every single
live pipeline run this whole session showed `[manager] running on backup:
Free Manager Council` — there is no configured Anthropic key here, so the
Council handles 100% of manager-tier calls (review + synthesis) for every
request. Investigated for concrete weaknesses rather than guessing at
"stronger," found four real ones, fixed all four:

**1. The Synthesizer (gemma_4 / google/gemma-4-31b-it:free) failed 100% of
live calls this session.** Every single test run showed the same two-step
failure: OpenRouter's "Google AI Studio" backend 429s (rate-limited), then
its own "OpenInference" fallback backend 404s ("Model not found"). Checked
this against OpenRouter's current catalog via web search before assuming
anything — the slug itself is real and current (released Apr 2026, still
listed) — this is a live OpenRouter-side free-tier routing degradation, not
a stale/wrong model name on our end. Bigger blast radius than just the
Council: grepping for `gemma_4` found it was ALSO `vibemind/brain.py`'s
`BRAIN_MODEL` — VibeMind's actual planning model, with the same problem
(every `plan_task`/`chat_brain` call paid a guaranteed-fail round trip
before `generate_resilient()` fell through) and one call site
(`run_desktop_agent`) that calls `registry.get(BRAIN_MODEL)` directly with
**no resilient wrapper at all** — with Ollama unreachable, that whole
feature would fail outright with zero fallback. Fixed both call sites:
swapped to `glm_47_flash_zai` (Z.AI), a model already proven working in
production per earlier DECISIONS.md entries and `ai_activity.log`.

**2. `_review_consensus` had no fallback, unlike the 5-stage pipeline.**
`_run_stage` (used for Plan/Draft/Critique/Refine/Synthesize) already tried
all 5 Council members in a smart fallback order when one died. But
`_review_consensus` (used for every quality-gate review — arguably the more
consequential path, since it decides APPROVE/REFINE/ESCALATE) only ever
asked two fixed roles, Critic and Refiner, with no substitution — if either
one's provider was rate-limited (observed live, repeatedly, for Cerebras
this session), review quietly dropped from 2 verdicts to 1, or to 0 (a hard
failure) if both died in the same window — a real risk, since Groq+Cerebras
correlated outages are already documented elsewhere in this repo as
something that's happened live before. Fixed: each of the 2 "independent
opinion" slots now falls back through the same `_FALLBACK_ORDER` rotation,
tracking which roles already succeeded (`used_roles`) or failed this round
(`dead_roles`) so the two verdicts stay genuinely independent and a role
that already failed for one slot is never retried for the other.

**3. Gemini quota contention.** `gemini_flash`, `gemini_flash_prompt`, and
`gemini_flash_vision` are three registry slots for the exact same
underlying `gemini-2.5-flash` model — Google's free tier quota key is
`GenerateRequestsPerDayPerProjectPerModel` (~20/day), scoped per (project,
model), not per our internal registry slot name, confirmed by the actual
429 error body live. Brain team, vision team, prompt-refiner's context
enricher, AND the Council's Planner role were all four drawing from that
one shared 20-request bucket — watched it exhaust mid-session. No second
Google API key exists to split the load (checked `config/settings.py` —
only one `gemini_api_key` field). Fixed the piece actually in scope for the
Council: added a new `gemini_flash_council` registry entry pointing at
`gemini-2.0-flash` instead — a genuinely separate quota bucket (proven
already, since `gemini_20_flash_ocr` already uses it successfully) — and
reassigned the Council's Planner role to it, cutting Planner out of the
4-way contention entirely without needing a new key.

**4. Prompts had no shared anti-hallucination bar.** Every concrete
hallucination bug found and fixed earlier this session (fast-path
"I can't generate images," escalation's invented "paste this into
Midjourney" advice, Drafter dropping a real image URL for prose) was the
same underlying failure shape: a free-tier text model filling a gap with a
plausible-sounding fabrication instead of using what was actually in front
of it or admitting uncertainty. Rather than only patching each instance
after the fact, added a general `_QUALITY_BAR` rule ("never invent a
capability/URL/fact that isn't evidenced by the input; if genuinely
uncertain, say so") to every content-touching stage (Planner, Drafter,
Critic, Refiner, Synthesizer) so the same failure shape is less likely to
need catching one specific case at a time going forward.

**Bonus find while in there: the roster signature string was hardcoded in
three separate places** — this module's own docstring, the Synthesizer's
system prompt (which the model was trusted to type back verbatim at the end
of every response), and `tools/manager_fallback.py`'s `active_name`
property — and had drifted out of sync with reality in two of five slots
(said "Llama 4 Scout" for Critic, actually `gpt_oss_120b_debug` since Llama
4 Scout was deprecated by Groq 2026-06-17; said "Gemma 4 31B" for
Synthesizer, the model just fixed above) for who knows how long, shown to
users on every single response. Replaced all three with a single
`FreeManagerTeam.roster_summary()` built from the live `COUNCIL` list, with
the signature line appended in code (`_with_signature()`) after the
Synthesizer's output rather than trusted to a model to reproduce a literal
string — the exact mechanism that let it drift in the first place.

**Verified live**: printed `roster_summary()` and `active_name` — both now
read "Gemini 2.0 Flash + GPT-OSS 120B x2 + GLM 4.7 + GLM 4.7 Flash,"
correct and in sync. Ran a real `free_manager_team.generate(...)` call
end-to-end: Council activated, Drafter (Groq) answered, Synthesizer
attempted `glm_47_flash_zai` — which surfaced a **real, separate** gap:
this environment has no `ZAI_API_KEY` configured. Confirmed this is a
strict improvement over the old state regardless: gemma_4's failure was a
slow live network round-trip (429 then 404, multiple seconds); the missing
key fails instantly (a local config check) and falls through cleanly to
`gpt_oss_120b_debug` with zero functional regression — the fallback
hardening in fix #2's spirit already absorbed this gap gracefully. Flagged
the key requirement to the user directly rather than silently declaring
victory; user chose to go get the free key (z.ai/model-api, no card) — full
re-verification pending that.

198 offline tests pass (6 new: roster_summary dedup, no Council member or
VibeMind's BRAIN_MODEL still points at the confirmed-broken gemma_4, review
consensus substitutes when both primaries fail, never retries a role that
already failed this round, and still raises when every member fails).

**Follow-up, same day: the ZAI_API_KEY gap, and a second real bug behind it.**
User got the free key and added it to `.env`. Re-verifying surfaced that
the key itself worked (no more auth error) but `glm_47_flash_zai` now
returned **HTTP 200 with 0 chars** — twice in a row, ~7-84s each. The
empty-response hardening added moments earlier in this same session (fix
#2's sibling in `_run_stage`) caught it immediately and correctly: logged
"empty response — trying next member," fell through to `gpt_oss_120b_debug`
in ~7.5s instead of silently eating 84s of dead air. That hardening paid
for itself on the very first real key it was tested against.

But "gracefully degraded" isn't "actually working," so kept digging instead
of accepting the fallback as good enough. Isolated the model with direct
probes bypassing our connector: a completely trivial prompt ("Say hello in
one word") with no system prompt still returned 0 chars at max_tokens=50
and max_tokens=400 — ruling out anything specific to the Synthesizer's
system prompt. Fetched the RAW OpenAI-client response object (not just
`.content`) at max_tokens=2000 and found the actual cause:
`message.reasoning_content` held 185 tokens of visible chain-of-thought
("1. Analyze the Request... 4. Final Decision: Hello"), while
`message.content` was "Hello" -- correct, but only because 2000 tokens was
enough to finish reasoning AND still have room to write the answer. GLM-4.7
"thinks compulsorily" per Z.AI's own docs, and web research (checked before
assuming a fix would work, not guessed) confirmed Z.AI's documented
`thinking: {"type": "disabled"}` toggle is a live, currently-unresolved
no-op bug on their API per multiple independent 2026 reports — not
something we can just turn off. `models/connectors/zai_conn.py` was
forwarding whatever `max_tokens` the caller passed (the Synthesizer role
calls with 400-1000) straight through, so reasoning alone could and did
consume the entire budget, leaving nothing for the visible answer.

Fixed at the connector level: `zai_conn.py._call` now floors max_tokens to
2000 (`_MIN_TOKENS_FOR_THINKING`) regardless of what the caller requested,
capped at the existing 16,384 ceiling — since thinking can't be reliably
disabled, undercutting the caller's budget just reproduces the same
silent-empty failure no matter what value they pass. This is
connector-level, not caller-level, because every current and future caller
of this model_id shares the exact same reasoning-budget requirement; fixing
it once here is safer than remembering to raise max_tokens at every call
site.

**Verified live, a third time**: `glm_47_flash_zai` now returns real content
(516 chars, 0 fails) for the exact same Synthesizer call that returned 0
chars twice before the connector fix. `roster_summary()` / `active_name`
still read "Gemini 2.0 Flash + GPT-OSS 120B x2 + GLM 4.7 + GLM 4.7 Flash" —
genuine 5-provider diversity (Google, Groq, Groq, Cerebras, Z.AI) now
actually functional end-to-end, not just configured.

201 offline tests pass (3 more: small max_tokens gets floored to the
thinking budget, large max_tokens passes through unchanged, both by
injecting a fake OpenAI client into the real connector and inspecting the
kwargs it actually sent — not just asserting on the return value).

**Process note, a third time this session:** this is the same lesson
again, one layer deeper. "The empty response is now handled gracefully"
would have been a perfectly reasonable place to stop — the system was, in
fact, no longer broken. Digging one level further (why is it empty at all)
found a real, fixable root cause instead of a permanently-degraded
5th-of-5 Council member quietly relying on its fallback forever.

## 2026-07-13: a genuinely different bug — the image opened by the IDE was never a Council/DesignTeam problem

User tried "create an image of a robot reading a book" as one of the test
prompts suggested for the just-hardened Council, typed directly into the
CLI (not the `/image` command). The IDE opened
`workspace/robot-reading-book.html` and the embedded image didn't load.
Worth being precise about scope here: this went through a completely
different pipeline than everything fixed above. Plain CLI input routes to
`core/agent_loop.py`'s `AgentLoop` (the general coding-agent loop — the
same infrastructure investigated at the very start of this session for the
letter-hijacking bug), NOT through `manager/claude_manager.py`'s
`handle_user_request()` team pipeline the Council hardening and the earlier
6-layer DesignTeam fix both targeted. Two separate entry points into image
generation exist in this codebase (the coding agent's own `design_asset`
tool, and the manager pipeline's DesignTeam) and this was the other one —
important not to conflate "I tested X" with "I tested every path to X."

Read the generated HTML: `<img src="/images/gen-659616b7d4.jpg">`. Checked
whether the file actually existed anywhere — `workspace/public/images/
gen-659616b7d4.jpg`, 81,305 bytes, a REAL and CORRECT image (viewed it: a
detailed, accurate robot sitting in an armchair reading a book in a
library). So the generation pipeline (the coding agent's `design_asset`
tool, which builds its own TaskJSON directly and calls DesignTeam,
bypassing the classification chain fixed earlier entirely) worked
perfectly. The bug was purely in `core/agent_loop.py::_localize_remote_images`,
which downloads every `image.pollinations.ai` URL referenced in project
files into `public/images/` and rewrites the reference to a local path —
but that rewrite was unconditionally `f"/images/{name}"`, a root-absolute
path. That's only correct when a bundler dev/build server maps `public/` to
the served root (Vite/CRA/Next.js — the convention the function's own
docstring describes: "rewrite the references (JSX src, CSS url())"). This
task produced ONE bare standalone `.html` file with no framework, no
`package.json`, no dev server at all. Opened directly (file:// or a naive
static server rooted wherever the `.html` happens to sit), `/images/...`
resolves to nothing — even though the actual file sat right there in
`public/images/`, downloaded correctly.

Fixed: added an `is_bundler_project` check (any `.jsx`/`.tsx` file among the
matched sources, or a `package.json` in the project root) — an unambiguous
signal, since only bundler-based projects use JSX/TSX or have a
`package.json`. Bundler projects keep the existing, proven-correct
`/images/{name}` absolute form. Everything else gets a path computed
relative to the REFERENCING FILE's own location
(`os.path.relpath(target, start=f.parent)`, forward-slash normalized),
which resolves correctly regardless of how the file is opened — file://,
a naive static server rooted anywhere, doesn't matter, since relative
paths are resolved against the referencing document itself, not a server's
root mapping.

Fixed the already-broken file directly too (`workspace/robot-reading-
book.html`'s `img src` and download `href`, both to `public/images/
gen-659616b7d4.jpg`) so the user's already-open file works immediately
without needing to regenerate anything.

**Verified via 4 new offline tests** (no network — the target image file
is pre-created so the test exercises path computation, not the curl
download): a bare HTML file gets the relative form; a project with
`package.json` keeps the absolute form; a project with a `.jsx` file keeps
the absolute form; a NESTED bare HTML file (`pages/sub/page.html`) gets the
correctly-prefixed `../../public/images/...` relative path, not just a
same-directory-assumption that would only happen to work for root-level
files.

205 offline tests pass (4 new, described above).

## 2026-07-13 (same day): the coding agent scaffolded a full Vite project for a one-picture request

User reported that a follow-up test ("generate a logo for a coffee shop
called Northwind") produced a full Vite/React project instead of just a
logo — the same category of missing-signal bug found and fixed three times
already this session (router_team.py's quick classifier, prompt_refiner's
5-stage pipeline, and now a THIRD, entirely separate location).

Read `core/agent_loop.py`'s own `_AGENT_SYSTEM` prompt (the coding agent's
"ROUTING RULES — decide what the user wants" section). It has: greetings,
code/build/debug, video/image analysis, web search, "LANDING PAGE / WEBSITE
/ UI tasks → design THEN code" (rule 5), remote server (rule 7), GitHub
(rule 8) — no rule at all for a bare image/logo/icon request. A bare
"generate a logo" ask doesn't cleanly match any rule except 5 (the closest
one that mentions `design_asset`), pulling in project-scale behavior by
association even though rule 5 itself only calls for HTML/CSS, not a
framework. Also noticed the prompt already said "DO NOT ask clarifying
questions for tasks 2-6" — referencing a rule 6 that didn't exist at all
(numbering jumped 5 → 7), confirming a rule was genuinely missing here, not
just under-specified.

Fixed: added rule 6, explicit that a bare image/logo/icon request must call
`design_asset` once (or a few times for variant options), never scaffold
any project or build tool, and either preview via a single flat `.html` or
just report the URL directly if no preview was asked for.

**Verified live**: ran `AgentLoop.run("generate a logo for a coffee shop
called Northwind", model_id="glm_47_cerebras")` against a clean temp
workspace. Result: `design_asset` called exactly once (not the 94-times
runaway case another comment in this file already documents), zero files
created (correctly — no preview was requested, so it just reported the
URLs), no scaffold, no `npm`/Vite anywhere in the trace. Downloaded one of
the returned URLs directly — a real, correctly-composed circular coffee-shop
logo (cup + wind swirls, brown/cream palette, exactly as prompted). Only
flaw: the rendered logo text reads "Nortttwild" instead of "Northwind" —
FLUX's well-known text-in-image garbling, already documented elsewhere in
this repo (the "garbled MPERIAN hero" incident) and unrelated to this fix;
not something a routing-rule change can address, since it's a rendering
limitation of the underlying image model, not a request-classification bug.

207 offline tests pass (2 new: the bare-image rule text is present in
`_AGENT_SYSTEM` and forbids scaffolding, and rule 6 is now actually wired in
between rules 5 and 7, closing the numbering gap the prompt already implied
existed).

## 2026-07-13: fixing the initial-commit code review findings

Full code review of the initial commit (121 files, ~27,600 changed executable
lines — see the review report itself for the complete findings list and
triage groups) surfaced 4 P0s, 18 P1s, 8 P2s, 1 P3. Fixed the P0s, every P1
with a concrete mechanical fix, and the cheap P2/P3 cleanups in one pass;
left the design-decision items (repetition-guard redesign, GitHub
confirmation-gate policy, VS Code extension pointing at a different
endpoint, the 4 file-size splits, the 4 missing-test-coverage modules) for a
separate, deliberate pass rather than rushing a structural decision.

**P0s, all fixed:**
- Both WebSocket routes (`/ws/{client_id}`, `/ws/agent/{client_id}`) had zero
  auth while every REST route used `Depends(require_token)`. Browsers can't
  set a custom `Authorization` header on a WS upgrade request, so the token
  now travels as a query parameter (`?token=...`), checked via
  `WebSocketException(code=WS_1008_POLICY_VIOLATION)` — the correct way to
  reject during a handshake, not `HTTPException`, which has no defined
  meaning there. Verified FastAPI's `dependencies=` really works on
  `@app.websocket` first (`inspect.signature`), then live end-to-end: reject
  with no token, reject with wrong token, accept with correct token, on both
  routes, plus confirmed the REST routes still work unchanged.
- `GET /api/fs/read` (vibemind/server.py) had no auth despite
  `read_text_file(path)` accepting any path with zero allowlist and
  returning raw file content — `.env`, SSH keys, anything the OS user can
  read. It was grouped with genuinely metadata-only routes (`fs_list`,
  `system_info`) by a comment that predates this being noticed; reading
  arbitrary file *content* isn't the same risk class as listing filenames,
  so it now requires `Depends(require_token)` like a mutating route would.
  `fs_list` deliberately left untouched — verified live it still returns 200
  with no token.
- Uncaught `RuntimeError` when every Free Manager Council member fails at
  the synthesis stage (a correlated outage this same log already documents
  happening live) propagated past every caller — this function, the REST
  route, the WS pipeline — turning a request whose `team_outputs` had
  *already been computed successfully* into a raw 500 that discarded that
  real work. Added a try/except around the `_synthesise` call site; on
  failure, `_degrade_to_team_outputs` picks the longest non-error team
  output and returns it with a one-line note, rather than throwing away
  work that already succeeded.

**P1s, all fixed:**
- Bearer token compared with `!=` instead of a constant-time comparison
  (both `api/server.py` and `vibemind/server.py` had the same pattern) — now
  `hmac.compare_digest`.
- `zai_conn.py`'s `_call_with_tools` never got the reasoning-token floor
  `_call` applies (see the 2026-07-13 entry above) — concretely live right
  now, since `vibemind/brain.py`'s desktop-agent fallback calls
  `generate_with_tools(..., max_tokens=1024)` on this exact model, below the
  floor. Same one-line fix as `_call`.
- No connector set an explicit client `timeout=`, relying on SDK defaults
  (~600s) and blocking the whole fallback chain on one stalled provider.
  `config/settings.py`'s `default_timeout_ms` existed for exactly this and
  was dead code — confirmed by grep before the fix, referenced by 10
  connectors after it. Ollama gets a separate, longer 90s timeout instead of
  inheriting the 30s default — a local model's first call can legitimately
  take ~45s loading into VRAM (documented elsewhere in this repo), and the
  generic floor would cut that off, not just a genuine hang.
- `huggingface.py`'s `_generate_image` called the synchronous
  `InferenceClient.text_to_image` directly inside an async function,
  blocking the whole event loop for the call's duration. Wrapped in
  `asyncio.to_thread`; verified live with a fake client that sleeps
  synchronously — a concurrent ticker coroutine completed all its ticks
  while the "slow" call was in flight, proving the loop wasn't blocked.
- `core/agent_loop.py`'s `_is_hard_reasoning` branch had no timeout on its
  `reasoning_core.reason()` call, while the sibling `_needs_planning` branch
  15 lines later wraps the same shape of call in
  `asyncio.wait_for(timeout=25.0)` with a comment explicitly warning about
  this exact risk. Now wrapped the same way.
- `ssh_exec` had none of `bash()`'s destructive-command tripwires
  (`_BLOCKED_RE`) — a "deploy to server" task could run `sudo rm -rf`,
  `shutdown`, etc. unfiltered over SSH. Reused `ToolExecutor._BLOCKED_RE`
  directly (a class attribute, no instantiation needed) rather than a
  second copy of the pattern list drifting out of sync with the first —
  exactly the failure shape this repo's own history already shows
  repeating. Verified live with a fake SSH session whose `.run()` raises if
  ever reached: a blocked command never got that far; a benign command did.
- `list_dir`, `build_repo_map`, and `core/verifiers.py::_iter_files` all
  fully materialized the entire tree via `sorted(root.rglob("*"))` —
  including `node_modules`/`dist`/`build`, this codebase's own comments
  calling them "thousands of generated files" — before the exact same
  `_IGNORE_DIRS` filter discarded them; descending in and immediately
  backing out was the expensive part, not the filter. Rewrote all three
  with `os.walk` and in-place `dirnames[:]` pruning, which never descends
  into an ignored directory at all. `list_dir` also dropped a second full
  `rglob('*')` that ran just to print an exact truncated-item count.
  Verified live (a real `node_modules/pkg/index.js` present, never appears
  in any of the three outputs) and all existing tests (including the
  `.vibeai`-journal-hidden test) still pass unchanged.
- `/api/agent`'s `AgentRequest` had no `context`/`history` fields — `cli.py`
  always passes both (context is the `/context` command's grounding text
  that stops the agent inventing an unrelated theme for an existing
  project) — so API/VS-Code-driven runs got a materially worse result than
  identical CLI-driven runs of the same backend. Added both fields with
  safe defaults, passed through to `loop.run()`. Fixed the WS agent path
  (`_run_agent_ws`) the same way, and, since it was right there: validated
  its payload against the same `AgentRequest` model instead of raw
  `dict.get()` calls with silent defaults — a malformed WS payload used to
  silently become `task=""` instead of a validation error.

**P2/P3s fixed (the cheap ones):**
- `tools/image_gen.py`'s `IMAGES_DIR` moved from `~/.vibeai/images` (outside
  `DEFAULT_WORKSPACE`, invisible to any agent file tool) to
  `DEFAULT_WORKSPACE / "generated_images"` — visible to a later same-session
  agent task, still in its own clearly-separated subdirectory rather than
  mixed into arbitrary project files.
- Added `response_model` to `/api/video`, `/api/screenshot`,
  `/api/manager/recover`, and `/api/agent` (previously only `/api/prompt`
  and `/api/image` had one) — matches each handler's actual existing return
  shape exactly, no behavior change, just OpenAPI-enforced contracts on the
  routes that were missing them.
- Standardized `/api/agent`'s status field from `"complete"` to `"ok"`,
  matching the two other status-bearing routes.

**Left for a deliberate follow-up pass, not rushed:** the repetition guard's
blind spot on identical multi-tool-call bundles, the GitHub tool's missing
confirmation gate before merge/release (a policy decision, not a one-line
fix), pointing the VS Code extension at `/api/agent` instead of `/api/prompt`
(needs the extension's own UX to decide how it surfaces file-edit progress),
the two 1000+-line file splits (`agent_loop.py`, `cli.py` — genuine
structural decisions on where the seams go), and the four modules with zero
test coverage on "must not regress" functionality
(`reasoning_core.ReasoningCore`, `core/verifier.VerificationEngine`,
`tools/manager_fallback.ManagerFallbackChain`, `teams/design.DesignTeam`).

**Verified throughout, not just at the end:** every fix was compiled, then
either live-tested directly (WS auth, fs_read auth, SSH tripwire, event-loop
non-blocking behavior, directory-walk pruning) or unit-tested against the
actual changed code path, with the full suite re-run after each one — not
batched to the end, so a broken fix would have been caught immediately
next to its own change rather than buried in a final mega-diff.

240 offline tests pass (33 new, one class per finding fixed, described
above).
