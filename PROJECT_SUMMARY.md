# VibeAI — Project History & Handoff

Written 2026-07-16 to hand off a long working session into a fresh conversation. Read this top to bottom for full context, or jump straight to the "Resume prompt" at the bottom and paste it into a new chat.

---

## 1. What VibeAI is

VibeAI is a multi-provider, free-tier LLM orchestration system at `c:\VS Codes\vibe_ai_v4\vibe_ai`. Start it with `python main.py`.

- Aggregates ~12+ distinct free models across Google, Groq, Cerebras, OpenRouter, NVIDIA NIM, Mistral, Z.AI, Pollinations, Ollama, and Anthropic (optional paid key). The model registry (`config/models_config.py::MODEL_REGISTRY`) currently has **38 entries** (model × provider × role slots) — re-check `len(MODEL_REGISTRY)` in any new session since this grows often.
- Organized into **teams**: brain, code, vision, design, router, prompt (enhancer/refiner), manager.
- **Free Manager Council** (`manager/free_manager.py`) is the primary "manager" whenever no valid Anthropic key is configured — a 5-stage Plan → Draft → Critique → Refine → Synthesize pipeline across free models. This is deliberate: `ANTHROPIC_API_KEY` is intentionally invalid in this project so the Council stays primary. Don't "fix" that.
- `core/agent_loop.py` is the coding-agent loop: golden vite scaffolds, a build gate, a deterministic verifier battery (`core/verifiers.py`), image localization, a project ledger, and a reflexion critic.
- Tests: `pytest tests/test_core_logic.py -q` — **299 passing**, all offline/deterministic (no network, no live keys needed). Re-check this count too.
- `DECISIONS.md` is the running log of provider facts/limits/decisions for this project — check it for prior context before assuming something about a provider's behavior.

This file (`PROJECT_SUMMARY.md`) itself is new — it did not exist before this session.

---

## 2. Chronological work log (this session, in order)

### 2.1 — 3D architecture demo (early, for a college presentation)
Built `VibeAI_3D_Architecture_Demo.html`, a single-file Three.js interactive visualization of the whole system for a presentation. Iterated on correctness (directional arrows between components, Council rendered as the literal visual center) and styling (used AI-generated reference images to guide the look). **This file is still untracked in git** (`??` in `git status`) — never committed, presumably intentionally kept out of the repo history as a demo artifact. Leave it alone unless asked.

### 2.2 — Confidence-triggered peer consultation
User asked for: "if the AI thinks its confidence for a part of its task is low, other AI models can help it." Built as an **opt-in** mechanism:
- `core/peer_consult.py` — `CONFIDENCE_PROMPT_SUFFIX` (invites a model to emit a `CONFIDENCE:`/`UNCERTAIN:` tag), `consult_if_unsure()` (parses the tag, asks a same-team peer for help if present), reuses `_LLM_PROVIDERS` from `models/registry.py`.
- Wired into `teams/base_team.py::BaseTeam.run()` — runs after `_execute()`, on every team, but is a no-op unless the model actually emitted the tag.
- Along the way, fixed a real bug in `models/base.py::generate()`: it returned `""` as a *successful* result instead of raising. Found live while testing peer_consult (Cerebras intermittently returned 0 chars on longer prompts). Now raises `RuntimeError` on empty/whitespace output so the existing retry/circuit-breaker/fallback machinery handles it like any other failure.

**Known gap, not yet fixed:** 5 of 9 reviewers in a later code review (`ce-code-review`, see §2.5) independently flagged that `CONFIDENCE_PROMPT_SUFFIX` is dead — it's defined but nothing actually appends it to a system prompt anywhere, so the tag this whole mechanism depends on is never emitted. This was reported to the user as a deferred design decision (which team/call-site should own appending it) and deliberately **not** unilaterally fixed.

### 2.3 — Confidence-gated model cascade
User's pick from a follow-up "make it stronger" list: run the cheapest/fastest model first, have an independent Verifier score confidence against a rubric, escalate to a stronger tier only when confidence is below threshold.
- `core/confidence_cascade.py` — `run_cascade()`, `CascadeResult` dataclass, `_score()`.
- Wired into `teams/code.py::_generate()` for the non-image SIMPLE/MODERATE path: `run_cascade(tiers=["llama33_70b_coder", primary_tier], ...)`.
- **Side effect / known gap:** adding the cascade here required removing the `CONFIDENCE_PROMPT_SUFFIX` import from `teams/code.py` — this is part of why that tag is now dead code (see §2.2's gap).
- Later hardened (see §2.5): the verifier call inside `run_cascade` had no try/except, so a verifier outage would discard an already-successful generation. Fixed to accept the tier's output as-is if the verifier call fails.

### 2.4 — Persistent cross-session lessons
User's pick from the same follow-up list: "close the loop from Verifier-approved output back into a real model — take the good traces too," adapted to close a real gap in `core/agent_loop.py` (its build-fix lessons only lived for the session, never persisted).
- `tools/memory.py` (ChromaDB + sentence-transformers) already existed; `core/agent_loop.py` gained a `lessons_ctx` retrieval block before building messages, and a `_store_build_lesson()` call fired after a build transitions from FAILING to PASSING.
- Added `_ensure_memory_ready()` helper (lazy-init + `asyncio.wait_for(timeout=10.0)`) shared by both memory call sites.

### 2.5 — `/compound-engineering:ce-code-review` on the above 3 commits
Ran the full slash-command code review across the peer-consult, cascade, and lessons-persistence work. Real issues found and fixed:
- **`tools/memory.py` was fully synchronous inside `async def`** — zero internal `await`s, so `asyncio.wait_for(timeout=10.0)` around `init()`/`retrieve_context()`/`_embed()` could never actually cancel anything. Fixed via `asyncio.to_thread`. Found independently by 3 reviewers (correctness, performance — escalated to P0, reliability); performance additionally traced that `api/server.py`'s lifespan never calls `manager.startup()` at all, making this the *only* init path under `serve` mode.
- Fixed a latent bug in `store_error_fix` that tested the `_embed` *method object's* truthiness instead of the computed embedding (always truthy, so the check never fired as intended).
- `run_cascade`'s verifier-swallowing issue (see §2.3) fixed here.
- The dead `CONFIDENCE_PROMPT_SUFFIX` tag (§2.2) was found here too — explicitly reported and left as a deferred decision, not committed to.
- Also identified but deliberately not fixed: cross-project memory leakage (no workspace scoping on the vector store), and a verifier-can-resolve-to-the-same-model-via-fallback edge case. Both explicitly reported to the user as deferred.

### 2.6 — CLI startup latency ("takes so much time to open")
Root-caused to `manager.startup()` synchronously awaiting `memory.init()` before the CLI became interactive.
- `manager/claude_manager.py::startup()` now backgrounds it: `self._memory_init_task = asyncio.create_task(self._init_memory_background())`.
- Separately found `HF_TOKEN` never reached `huggingface_hub` (pydantic-settings doesn't touch `os.environ`) — fixed with an explicit export in `config/settings.py` after `Settings()` is constructed.

### 2.7 — Bash tool hanging on `npm create vite` (real pasted log, "why vide is not working here?")
Root-caused to missing `stdin=` on subprocess spawns — the child process inherited the parent's stdin and hung on Vite's interactive "directory not empty" prompt.
- `tools/agent_tools.py`: both `_run_shell()` branches (`create_subprocess_exec` and `create_subprocess_shell`) and `git()` now pass `stdin=asyncio.subprocess.DEVNULL`.
- Live-verified both the repro (now fails fast with "Operation cancelled" instead of hanging) and the happy path (still works normally).

### 2.8 — "openstack" website generation completely broken (strong user frustration)
User: the generated site "does nothing," expected the AI team to beat Sonnet 5 at web building and it clearly wasn't. User picked "fix the tool-calling bug" (over "just patch openstack") via a direct choice.
- Root-caused via real log analysis (`logs/vibeai_cli.log`) to `llama33_70b_coder` hitting Groq's `400 tool_use_failed` 5 times in a row. The existing recovery parser couldn't salvage it, and its fallback (a plain text return) was invisible to the agent loop's only escalation trigger (a length-based empty-response guard) — so the loop just kept retrying the same broken path.
- `models/connectors/groq_conn.py`: the 400-recovery fallback now returns an explicit `{"type": "text", "content": ..., "tool_call_failed": True}` marker instead of a signal-less dict.
- `core/agent_loop.py`: new `_tool_fail_streak` counter — escalates to `_next_fallback_tier(...)` after 2 consecutive `tool_call_failed` responses (faster than the empty-response guard's threshold of 3, since this is unambiguous infrastructure failure, not just a weak answer). Resets to 0 whenever `tool_calls` is actually present.

### 2.9 — "Council v3" external design doc (pasted in full, with an explicit "follow this strictly" instruction)
The doc claimed 5 "mistakes" in the Free Manager Council with "exact fixes": Intent Contract, acceptance criteria, heartbeats/supervisor-pings, blind cross-family critique, replan delta — plus upgrade ideas (ambiguity gate, complexity gate, calibration loop).

Before implementing anything, fact-checked the doc against the real code and pushed back on two points:
- Its "intent evaporation" claim was **wrong** — the verbatim user request is already passed to every Council stage. Did not build a fix for something that wasn't broken.
- Its "heartbeats/supervisor-pings" design assumed an execution model (long-running async sub-agents reporting progress) that **doesn't exist** in this codebase — each Council stage is one single model call, not a multi-step task. Reframed as a **stage-checkpoint** system instead (check alignment after each stage completes, before committing to the next one) and explained the reframing to the user, who accepted it by not objecting.

User's explicit pick after the pushback: "add the execution model and use the fix, don't touch the parts that are already fixed." Built:
- `manager/intent_contract.py` — frozen `IntentContract` dataclass (`goal`, `success_criteria`, `constraints`, `non_goals`, `open_ambiguities`), `build_intent_contract()` (uses `llama31_8b_router`, fails open to a bare-goal contract on any error).
- `manager/supervisor.py` — `SupervisionVerdict` dataclass, `supervisor_ping(contract, stage_role, stage_output)` (checks a stage's output against the contract, recommends `continue`/`correct`/`escalate`; fails open to `continue`), `log_verdict()` writing to `logs/supervisor_verdicts.jsonl` (best-effort).
- `manager/free_manager.py` rewritten: `_collaborative_pipeline()` now builds an `IntentContract` first — if it has open ambiguities, returns a clarifying question immediately, no stage runs. Otherwise runs a real `_classify_complexity()` gate (`llama31_8b_router`, fails open to "complex" — the *safer, more expensive* default — on any error) to pick the 2-stage fast path or the full 5-stage path. Every real stage except the final Synthesizer now goes through `_run_stage_supervised()` (calls the stage, pings the supervisor, retries once on `"correct"` with drift feedback appended, or calls a non-primary candidate on `"escalate"`).
- Also swapped the Critic's model in the `COUNCIL` roster (was accidentally the same model family as the Drafter — a same-family blind spot; now a different family, `qwen36_27b_verifier`) and rewrote the Critic's system prompt to require concrete evidence (severity, exact location, violated requirement, specific fix) instead of vague impressions.
- Committed as `7e82075` ("Add confidence-gated Council v3: intent contract, gates, stage supervision").

### 2.10 — A second external doc, mid-turn, recommending specific models per team (Kimi K2.6/K2.7, DeepSeek V4 Pro, GLM 5.2, etc.)
Fact-checked live against the real OpenRouter catalog (`curl https://openrouter.ai/api/v1/models`) rather than trusting the doc:
- **No Kimi models exist** in the live free catalog.
- DeepSeek confirmed paid-only on OpenRouter (already present in this project via NVIDIA NIM anyway).
- The doc's claim that "Qwen3 Coder's `:free` endpoint on OpenRouter is gone" is **factually wrong** — verified live twice, same session, that it exists and responds (just rate-limited: two live calls both failed with 429 "temporarily rate-limited upstream," ~155s and ~183s to give up).
- No evidence GLM 5.2 is free-tier accessible for this project.

Told the user directly this doc's specific model names would not be used, and continued with an independently-verified plan instead.

### 2.11 — Team Leaders + CEO hierarchy (the last completed feature, this session's final task)
User: give every team a Leader (manager/supervisor/decision-maker for that team), and give the Manager Council a CEO that oversees and reports on whether the AI models are working well — "an organized hierarchy."

Built and shipped, **committed as `59dc181`**:
- **`config/models_config.py`** — 2 new registry entries, both live-verified against real APIs before use:
  - `nemotron_ultra_ceo` (`nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter) — confirmed working, 19.9s real response time. Reserved for the CEO role specifically because of that latency: a 550B model must never sit on the per-request path.
  - `qwen3_coder_openrouter` (`qwen/qwen3-coder:free` via OpenRouter) — registered honestly as manual-select-only after live-verifying it exists but failed twice with 429 rate-limit errors. **Not** used for any mandatory role because of that unreliability.
- **`teams/leadership.py`** (new) — `TEAM_LEADERS` maps each team to its own already-proven model elevated into a review role: `gemini_flash` (brain), `glm_47_cerebras` (code), `gemini_flash_vision` (vision and, borrowed, design — design has no vision model of its own), `gpt_oss_120b_dispatch` (router). `leader_review(team_name, instruction, output)` asks that model to approve or reject with a specific fix; fails open to `approved=True` on any error or unconfigured team. `log_verdict()` writes to `logs/leader_verdicts.jsonl`.
- **`teams/base_team.py::BaseTeam.run()`** — after the existing `consult_if_unsure()` call, every team's output now goes through `leader_review()`. On rejection: exactly **one** bounded revision retry (re-runs `_execute()` with the Leader's feedback appended to the instruction), no second Leader re-check — this matches the project's established "hard cap" convention used everywhere else (reflexion cycles, cascade escalation, supervisor stage retries).
- **`manager/ceo.py`** (new) — `generate_oversight_report()`: **not** a per-request gate (would put the 550B model in the path of every response, defeating the point of a free-tier system). Reads real accumulated signal — `logs/supervisor_verdicts.jsonl`, `logs/leader_verdicts.jsonl`, and the Council's own `status()` call/fail counters — and asks `nemotron_ultra_ceo` to synthesize a short human-readable health report. On-demand only.
- **`cli.py`** — new `/ceo` slash command (mirrors the existing `/think` pattern), plus a help-text entry.
- **Tests** — 15 new tests across `TestLeaderReview`, `TestBaseTeamLeaderWiring`, `TestCeoOversightReport` in `tests/test_core_logic.py`, following the project's established `monkeypatch.setattr(module, "generate_resilient", fake)` convention. All fail-open paths, the log-write paths, and the bounded-retry wiring are covered.

**A real bug was caught by live end-to-end testing, not by the unit tests:** running `RouterTeam().run(...)` for real (not mocked) showed the Router's Leader rejecting a *correct* routing-classification JSON output, reasoning "the output does not include the requested prime-checking function code." The Leader was judging the output against the user's raw top-level request instead of against what the Router team is actually supposed to produce (a classification decision, not a solved problem). Fixed by adding a `TEAM_MANDATES` mapping (what each team's real job is, distinct from the raw request) injected into the Leader's system prompt. Re-verified live after the fix — the same scenario now correctly approves on the first pass, citing the right reason. Added a regression test (`test_system_prompt_carries_team_mandate_not_just_raw_instruction`) pinning this.

Final state before commit: **299/299 tests passing**, both new registry entries live-verified, `leader_review` live-verified twice more (correctly approved good code, correctly rejected a deliberately-broken function with an actionable fix), the CEO report live-verified (produced a real, calibrated synthesis correctly identifying the Council as dormant from real log data — not a canned response). `git diff --stat` confirmed the diff was scoped to exactly the 6 intended files before staging. Committed only after explicit user sign-off.

---

## 3. Files created or modified this session

**Created:**
- `VibeAI_3D_Architecture_Demo.html` (untracked, not committed — presentation artifact)
- `core/peer_consult.py`
- `core/confidence_cascade.py`
- `manager/intent_contract.py`
- `manager/supervisor.py`
- `teams/leadership.py`
- `manager/ceo.py`
- `PROJECT_SUMMARY.md` (this file)

**Modified (committed):**
- `teams/base_team.py` — `consult_if_unsure` wiring, then `leader_review` wiring
- `models/base.py` — empty-response now raises instead of silently succeeding
- `teams/code.py` — cascade wiring for SIMPLE/MODERATE path
- `tools/memory.py` — `asyncio.to_thread` fix, `_embed` truthiness bug fix
- `core/agent_loop.py` — lessons retrieval/persistence, `_tool_fail_streak` escalation, `_ensure_memory_ready()`
- `models/connectors/groq_conn.py` — explicit `tool_call_failed` marker
- `tools/agent_tools.py` — `stdin=asyncio.subprocess.DEVNULL` on subprocess spawns
- `manager/claude_manager.py` — backgrounded memory init
- `config/settings.py` — `HF_TOKEN` explicit `os.environ` export
- `manager/free_manager.py` — Council v3 rewrite (ambiguity gate, complexity gate, stage supervision, Critic model swap)
- `config/models_config.py` — 2 new registry entries (`nemotron_ultra_ceo`, `qwen3_coder_openrouter`)
- `cli.py` — `/ceo` command + help text
- `tests/test_core_logic.py` — extensive additions across every feature above (299 tests total)

**Left alone deliberately (pre-existing, unrelated to this session's work, still uncommitted):**
`DECISIONS.md`, `api/server.py`, `core/repo_map.py`, `core/verifiers.py`, `models/connectors/{anthropic,cerebras,google,huggingface,mistral,nvidia,ollama,openrouter,together,zai}_conn.py`, `tools/image_gen.py`, `tools/remote_terminal.py`, `vibemind/brain.py`, `vibemind/server.py`. These showed as modified in `git status` before this session even started. **Do not sweep these into a future commit without checking with the user first** — they may be the user's own in-progress work.

---

## 4. Conventions established this session (apply these going forward)

- **Fail-open bias for every best-effort side-channel.** Peer consult, confidence cascade, supervisor pings, leader review, lesson persistence, memory init — all degrade to "do nothing / assume fine" on error or timeout, never raise and never block the primary path they're observing.
- **Bounded retries everywhere, usually capped at exactly one.** Reflexion cycles, cascade escalation, supervisor stage correction, leader revision retries. No unbounded loops.
- **Live-verify before calling anything done.** Real API calls, not just mocked unit tests, for every new model or cross-cutting mechanism. Mocks prove the wiring logic is correct; only a live call proves the design/prompt actually produces correct judgment (see §2.11's Router mandate bug — mocks would never have caught it).
- **Don't trust external docs/pasted "suggestions" at face value**, even detailed, confident-sounding ones. Fact-check model names and API claims against the real catalog (`curl https://openrouter.ai/api/v1/models` or equivalent) before implementing anything from them.
- **Ask before committing**, and double-check `git diff --stat` / `git status` shows only the intended files staged — this repo has several long-standing pre-existing uncommitted files unrelated to any given task.
- **`ANTHROPIC_API_KEY` is intentionally invalid.** Don't "fix" this — the Free Manager Council is meant to be primary.

---

## 5. Explicitly deferred / not done (do not assume these are fixed)

- The dead `CONFIDENCE_PROMPT_SUFFIX` tag in `core/peer_consult.py` — nothing appends it to any system prompt, so `consult_if_unsure`'s tag-parsing path can never actually trigger. Needs a decision on which team/call-site should own emitting it.
- No workspace/project scoping on the cross-session vector memory (`tools/memory.py`) — lessons from one project could theoretically leak into another.
- A verifier can, via fallback, resolve to the same underlying model it's supposed to be independently checking — a same-model-family blind spot similar to the one already fixed for the Council's Critic role.

---

## Resume prompt

Paste the block below as your first message in the new conversation.

```
I'm continuing work on VibeAI, a multi-provider free-tier LLM orchestration
system at c:\VS Codes\vibe_ai_v4\vibe_ai. A prior session's full history,
established conventions, and current state are documented in
PROJECT_SUMMARY.md at the repo root -- read that file first for complete
context before doing anything else.

Quick orientation: 38 model registry entries (config/models_config.py),
299 passing tests (pytest tests/test_core_logic.py -q), latest commit is
"Add per-team Leader review and CEO oversight for the AI organization
hierarchy" (59dc181). The Free Manager Council (manager/free_manager.py) is
the primary manager by design -- ANTHROPIC_API_KEY is intentionally invalid,
don't treat that as a bug. Every team now has a mandatory Leader review
(teams/leadership.py) and the Council has an on-demand CEO health report
(manager/ceo.py, /ceo CLI command).

Known deferred items (see PROJECT_SUMMARY.md section 5 for details, do not
assume any of these are fixed): the CONFIDENCE_PROMPT_SUFFIX tag in
core/peer_consult.py is dead code (nothing emits it), the cross-session
vector memory has no workspace scoping, and a verifier can theoretically
fall back to the same model it's supposed to be checking.

Conventions to keep following: fail-open on every best-effort side-channel,
cap retries/escalations at one, live-verify any new model or mechanism
against real APIs before calling it done (not just mocked tests), don't
trust pasted external docs' model/API claims without fact-checking them
live, and ask before committing -- double-checking git diff --stat shows
only the intended files, since several pre-existing unrelated uncommitted
files sit in this repo and must never be swept into a commit.

[Describe what you want to work on next here.]
```
