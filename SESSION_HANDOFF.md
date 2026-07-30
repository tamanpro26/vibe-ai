# Session Handoff

Written to hand this working session to another engineer (human or AI). Read
top to bottom. Section 1 is what changed and why, Section 2 is what is still
open, Section 3 is the traps that will waste your time if you do not know them.

**Repo:** `c:\VS Codes\vibe_ai_v4\vibe_ai`
**Branch:** `feat/website-showcase-chat`
**Tests:** `py -3.13 -m pytest tests/test_core_logic.py -q` -> **477 passing**
**Nothing in this session was committed.** All work is uncommitted in the
working tree. Review `git status` before you stage anything; this repo has
long-standing unrelated modified files that predate this session.

> **Interpreter:** use `py -3.13`, never bare `python`. Bare `python` resolves
> to a 3.14 install without this project's dependencies.

---

## 1. What was done

### 1.1 Search stack: DuckDuckGo replaced with Firecrawl + Exa

**Files:** `tools/search.py` (rewritten), `config/settings.py`, `.env`,
`.env.example`, `cli.py`, `tests/test_core_logic.py`

The old stack scraped DuckDuckGo HTML. It had no API contract and repeatedly
soft-blocked (`HTTP 202`) *entire query topics* under ordinary chat use, not
abuse. A retry and a second DDG endpoint were added as damage control earlier,
but scraping with no SLA cannot be engineered around. It is now gone.

Replacement, both live-verified before and after the rewrite:

- **Firecrawl** - primary. Real `/search` API, renders JS, returns clean
  markdown. Free tier 1,000 pages/month, **no card required**.
- **Exa** - neural/semantic search. Its own ranking **replaced the old
  LLM-based re-rank call**, removing one network hop and one failure point.

Merge order is Exa first (its ordering *is* the relevance signal, encoded as a
descending `relevance_score`), then Firecrawl fills remaining slots,
deduplicated by URL.

**Brave was evaluated and rejected**: it now requires a credit card even on the
free tier. Confirmed live against their own FAQ. `brave_api_key` still exists
in settings but is unused and marked as such.

Verified live three separate ways: isolated Bitcoin-price query (2.28s, 5 real
results), isolated Wimbledon query (correct answer, real BBC/CNN/ESPN
citations), and end-to-end through the CLI (real CoinGecko + Yahoo Finance
prices, mutually consistent).

### 1.2 Intent router: five separate routing gaps closed

**File:** `core/intent_router.py`

A recurring class of bug: a plain question was routed into the 25-iteration
coding agent, which then latched onto stale chat history and answered a
completely unrelated task. Each fix below was found by a real failing query,
not by inspection.

| Phrasing that failed | Cause | Fix |
|---|---|---|
| `derive the ... acceleration the car will move upwards` | physics word-problem matched the action verb "move" | reasoning-verb + math-noun escape to chat |
| `how did Rohit Sharma perform...` | `how` missing from `_QUESTION_LEAD` | added `how` |
| `provide me the last score of...` | imperative info-request not covered | added `provide me`/`give me`/`share`/`find out` |
| `i want the info of the scores...` | `i want` (no "to know") not covered | widened to general `i want`/`i need`/`i'd like` |
| `who actually won it though` | `who won` required adjacency | `_WHO_WON_RE` allows inserted words |
| `rohit sharma's last **cricket** match` | `last (match\|game)` required adjacency | `_LAST_NEXT_EVENT_RE` allows inserted words |

Also added `_YEAR_RE` + `_EVENT_NOUN_RE`: naming a dated event ("the 2026
Wimbledon final") now triggers live search even with no score/latest keyword,
because a model cannot self-assess whether real time has passed a date it was
trained before. It had been confidently claiming completed events "haven't
happened yet".

### 1.3 Chat live-search augmentation

**File:** `cli.py` (`handle_chat`)

`handle_chat` had **zero tool access by design**, so factual questions were
answered from stale training data. It now performs one bounded search when
`needs_live_search()` fires. Fail-open throughout: a timeout or error silently
falls back to a plain answer and never breaks the turn.

Three follow-on fixes, each from a real failure:

1. **Context-folded query.** A bare follow-up ("what was the final score")
   searched alone lost its subject and returned generic scoreboard homepages.
   The last two *user* turns are now folded into the search query.
2. **Grounding rule.** Correct retrieved context did **not** stop fabrication -
   the model invented a specific score and a fake `【4†source】` citation marker
   that appeared nowhere in the snippets. An explicit anti-fabrication rule was
   added to `_CHAT_SYSTEM`. This is the single most transferable lesson here:
   *retrieving the right chunks is not enough.*
3. **Anti-contradiction rule + cache.** When a same-topic follow-up's own
   search failed, the model reverted to training-time assumptions and
   contradicted its own correct prior answer. Added a "do not contradict
   yourself" rule plus a 10-minute same-topic result cache
   (`_LAST_SEARCH_*` in `cli.py`) so a failed second lookup reuses the earlier
   grounding instead of guessing.

### 1.4 Landing page (`Neuronova-vibeaiwebsite/`)

Treated as **Redesign - Preserve**: the cyan HUD-terminal identity is
deliberate and product-appropriate, so it was kept. The work was removing AI
tells, not restyling.

- **Stale factual claim fixed.** The site advertised "439 automated tests" in
  three places; the suite is at **469**. Verified by running it, not assumed.
  On a page whose pitch is "tested, not just demoed", this was the worst
  possible number to have stale.
- **32 em-dashes -> 0** across the landing surface, replaced contextually
  (period / comma / colon), preserving copy voice. Two were in `index.html`'s
  `<title>` and meta description, visible in the browser tab and Google
  results.
- **Removed the "SCROLL" cue** from the hero plus its orphaned CSS/keyframes.
- **`.team-card` side-tab removed** (`border-left: 3px solid var(--team)`).
  Free to delete: team colour is still carried by `.team-swatch` and the hover
  glow, so the bar was a third redundant encoding.

Created **`Neuronova-vibeaiwebsite/DESIGN.md`** documenting the real shipped
system (tokens, Chakra Petch / IBM Plex stacks, per-team status colours,
3-6px radii, glow micro-interactions) - derived from the code, not invented.

### 1.5 Smart City ESP32 (`c:\VS Codes\smart-city-esp32`)

Three real bugs fixed, all verified live on hardware:

1. **`esp32_connected` never true.** It required a `last_seen` heartbeat within
   15s, but the ESP32 is *designed* to stay silent between fire events. Now
   driven by the live `/status` poll succeeding.
2. **Dashboard hammered the board** with a new TCP connection every 600ms.
   Throttled to >=1.5s with a short cache.
3. **Root cause: `handleStatus()` used Arduino `String` concatenation** on the
   most-polled endpoint - classic ESP32 heap fragmentation. After sustained
   polling it took down the *entire* HTTP server, not just `/status`. Rewritten
   with a fixed `snprintf` buffer, zero heap allocations.

Full fire cycle verified end-to-end: normal -> trigger -> alert -> AI override
-> freeze + emergency lights -> clear -> resume. See
`smart-city-esp32/SPEC_STATUS.md` for the 5-requirement compliance matrix.

### 1.6 OmniRoute (`c:\VS Codes\OmniRoute`) - cloned and running, NOT integrated

Cloned from `https://github.com/diegosouzapw/OmniRoute`. It is an AI gateway
aggregating free tiers behind one OpenAI-compatible endpoint - directly
adjacent to VibeAI's problem space.

- Running at **`http://localhost:20128`**, API at `/v1`.
- Dashboard password set (see Section 3 for the non-obvious mechanism).
- **6 providers connected** via API using VibeAI's existing keys: gemini, groq,
  cerebras, openrouter, nvidia, zai. (mistral skipped - no key in `.env`.)

**Its value was overstated and this matters.** The advertised ~1.53B free
tokens/month is *not* something the gateway hands you. It is the documented sum
of provider pools **you connect yourself**. Before connecting anything,
`provider_connections` had **0 rows** and every one of the 8 no-auth provider
families failed a live completion test (DuckDuckGo anti-abuse challenge, Vercel
IP block, `ENOTFOUND opencode.ai`, `spawn EINVAL`, 500s). Those no-auth
providers work by reverse-engineering free web chat UIs and are inherently
fragile.

Also note: the providers now connected are largely the **same ones VibeAI
already calls directly**. Routing through OmniRoute does not unlock new
capacity there. What it *would* add is unified failover, usage tracking, and
its claimed 15-95% token compression - real value, but different from "more
tokens". Weigh that before building the integration.

---

## 2. What is still open

### 2.1 DONE - OmniRoute end-to-end verification

Completed after handoff was written. 4 providers tested with real
`/v1/chat/completions` calls (`stream` default, SSE reconstructed and
checked for real `content` + `finish_reason` + usage, not just HTTP 200):

| Model | Result |
|---|---|
| `auto/best-coding` (router) | resolved to `nvidia/llama-3.1-nemotron-nano-vl-8b-v1`, content `"OK"`, `finish_reason: stop` |
| `groq/llama-3.3-70b-versatile` | content `"OK"`, `finish_reason: stop` |
| `gemini/gemini-2.0-flash` | content `"OK"`, `finish_reason: stop` |
| `cerebras/zai-glm-4.7` | first attempt hit a transient per-model cooldown (`404` with `"reset after 1m 32s"`, NOT a real 404); retried after the stated window, succeeded with real content and usage |

One false lead worth knowing: guessing `cerebras/llama-3.3-70b` 404'd because
**that model ID does not exist in the current catalog**, not because cerebras
was broken. Real current cerebras models: `cerebras/gpt-oss-120b`,
`cerebras/gemma-4-31b`, `cerebras/zai-glm-4.7`. Always confirm an ID against
`GET /v1/models` before treating a 404 as a provider failure - OmniRoute
appears to reuse 404 for both "no such model" and "temporary cooldown",
distinguished only by the presence of a `reset after` string in the message.

**Gate cleared. Proceed to 2.2.**

### 2.2 DONE - VibeAI OmniRoute connector

Built, live-verified, and tested. **Suite is now 477 passing** (was 469).

Files touched:
- `models/connectors/omniroute_conn.py` (new)
- `config/settings.py` - `omniroute_api_key`, `omniroute_base_url`
- `.env` / `.env.example` - `OMNIROUTE_API_KEY`
- `models/registry.py` - import, `case "omniroute"`, concurrency cap 3
- `config/models_config.py` - `omniroute_auto_coding` entry (registry now 39)
- `tests/test_core_logic.py` - `TestOmniRouteConnector`, 8 offline tests

**Registered manual-select only.** `omniroute` is deliberately NOT in
`_LLM_PROVIDERS`, so escalation/peer-consult pools never auto-reach for it -
it depends on a local Node server started by hand. Same precedent as
`qwen3_coder_openrouter`. A test pins this.

**Two live-caught bugs are baked into the implementation.** Both presented as
an unexplained multi-minute hang, not an error, so they are worth knowing:

1. **`auto/*` never answers non-streaming.** `stream:false` -> HTTP 000, 0
   bytes, hangs indefinitely; the same request streamed returns immediately.
   Direct provider models (`groq/...`) *do* answer non-streaming in 0.73s, so
   this is specific to the auto router. The connector therefore streams
   **unconditionally** rather than branching on a model-name pattern that
   would rot as the catalog changes.
2. **`auto/*` routing is non-deterministic and sometimes picks a reasoning
   model.** Three consecutive identical calls landed on
   `llama-3.1-nemotron-nano-vl-8b-v1`, then
   `nemotron-3-nano-omni-30b-a3b-reasoning`, then `nemotron-nano-12b-v2-vl`.
   Reasoning variants emit `delta.reasoning` before any `delta.content`, so a
   small budget is spent entirely on hidden reasoning and the visible answer
   is empty. Measured: `max_tokens=16` -> content `''`; `max_tokens=400` ->
   `'OK'`. Empty is not harmless - `models/base.py` raises on empty output, so
   tenacity retried a ~40s call three times and the caller saw a 130s+ hang.
   Fixed by flooring `max_tokens` at 2048, the identical rule and rationale
   already in `cerebras_conn.py`.

Live result after both fixes, through VibeAI's own `generate()`:
**5/5 successful, ~0.9s each after warmup** (first call 6.94s cold).

**Still open on this:** only `auto/best-coding` is registered. If you want
VibeAI to reach OmniRoute's other pools, add more `ModelDef` entries. Confirm
any model ID against `GET /v1/models` first - a wrong ID returns 404, and
OmniRoute also returns 404 for a temporary cooldown (distinguishable only by a
`reset after` string in the message).

### 2.3 Landing page animations - REQUESTED, NOT STARTED

The user asked to "polish the landing page with cool animations and smoother
scrolling using lenis". Planning was done; **no code was written**.

Corrections to carry forward:
- There is **no "motion skill plugin"** installed. Installed animation-adjacent
  skills are `animation-vocabulary` (a naming glossary) and `review-animations`
  (a reviewer). Neither is a code library. The intended library is **Motion**
  (`motion/react`, ex-Framer-Motion).
- **Neither `motion` nor `lenis` is installed.** Both need adding.

Agreed motion thesis (from `impeccable/reference/animate.md`, which mandates
one authored focal moment, not per-section reveals):
- **Focal moment:** the hero network canvas already renders a node field. Make
  signal pulses travel the links and reroute around a dropped node - the
  product literally animating its own core truth (many providers, one answer,
  instant reroute). Not a generic fade-rise.
- **Continuity:** Lenis inertial scroll.
- **Feedback:** preserve existing hover/glow states untouched.
- Respect `prefers-reduced-motion`; keep content visible by default so a failed
  script cannot hide the page.

### 2.4 Decisions the user still owes

1. **`.bg-grid` decorative grid** (`src/App.css:13`). Flagged by both the
   impeccable hook and the taste skill as a decoration tell. Counter-argument:
   a HUD *is* an instrument surface, and it is subtle (`--line-soft` at 0.07
   alpha, radially masked). **Left untouched deliberately** - removing core
   identity to satisfy a linter, or silently suppressing a real finding, are
   both wrong without the user's call. Either drop it or scope an ignore.
2. **Section-number eyebrows** (`01`, `02`, `04`...) and the **div-based
   simulated terminal** in the hero. Both flagged as tells; the terminal is
   arguably legitimate since it is honestly labelled "simulated" and
   demonstrates the real product. Judgment calls, not hard bans.
3. **ESP32: which program to run for grading.** `controller.py` matches the
   literal spec (exits on NORMAL, total freeze); `dashboard.py` is the
   judge-facing multi-cycle UI and does **not** implement spec points 4 and 5.
   Both exist intentionally. See `SPEC_STATUS.md`.

### 2.5 Known-deferred, lower priority

- **`better-sqlite3` is unpinned** in OmniRoute - installed with `--no-save`.
  A future `npm install` reverts to the broken v13 (Section 3.2).
- First chat turn immediately after CLI startup can lose its search to
  event-loop contention with background memory/manager init. A throwaway first
  message avoids it. Diagnosed, not fixed.
- Chat model occasionally emits `【1】`-style citation markers despite the
  no-footnote rule. Cosmetic; content stayed accurate.
- `needs_live_search` does not fire for "who is the president of X" - a real
  gap, deliberately left rather than scope-creeping.
- Pre-existing: `CONFIDENCE_PROMPT_SUFFIX` in `core/peer_consult.py` is dead
  code; cross-session vector memory has no workspace scoping.

---

## 3. Traps that will waste your time

### 3.1 OmniRoute password: `INITIAL_PASSWORD` in `.env` does nothing

`src/lib/auth/managementPassword.ts` resolves
`storedPassword || INITIAL_PASSWORD`, and short-circuits entirely if the stored
value is already a bcrypt hash. The DB already had a stored password from first
boot, so **editing `.env` changes nothing and fails silently** - you restart,
see no error, and are still on the old password.

The password was set by writing a **bcrypt hash (12 rounds**, matching the
app's own `MANAGEMENT_PASSWORD_SALT_ROUNDS`) directly into
`key_value` where `namespace='settings' and key='password'`, JSON-encoded.
It was found stored in **plaintext** before this. Verified end-to-end:
login returns `200 {"success":true}`, old default returns `401`, and an
authenticated session reaches `/dashboard`.

### 3.2 OmniRoute will not boot on Node 24 out of the box

`package.json` pins `better-sqlite3@^13.0.1`, which publishes **zero prebuilt
binaries** (verified against its GitHub release assets). On Node 24 it falls
back to compiling from source, needing Visual Studio with the C++ workload -
not installed on this machine. Failure is `Cannot find module 'better-sqlite3'`.

Fix applied: **`better-sqlite3@12.11.1`**, which does ship
`...-node-v137-win32-x64.tar.gz` (ABI 137 = Node 24). OmniRoute's own
troubleshooting doc confirms Node 24 is supported with v12.x. Installed
`--no-save`, so **it is not persisted** - see 2.5.

### 3.3 Secrets are NOT in this file, deliberately

This file is not gitignored. Credentials live where they belong:

- `FIRECRAWL_API_KEY`, `EXA_API_KEY`, and all provider keys are in
  `vibe_ai/.env` (**confirmed gitignored** via `git check-ignore`).
- The OmniRoute dashboard password is stored bcrypt-hashed in its SQLite DB at
  `%AppData%\Roaming\omniroute\storage.sqlite`. It is not in plaintext on disk.
- The OmniRoute API key (`sk-13d2...`) was provided by the user in chat; it is
  a **localhost-only gateway key**. Retrieve it from the OmniRoute dashboard
  (Endpoints) rather than from any file.

Both the dashboard password and the OmniRoute API key were typed into the chat
transcript. They are low-risk (localhost only), but if either is reused
anywhere else, rotate it.

### 3.4 Standing conventions in this repo

- **Live-verify before calling anything done.** Mocks prove wiring; only a real
  call proves behaviour. Multiple bugs this session passed unit tests and still
  failed live.
- **Instrument before indicting the model.** Repeatedly this session, an
  apparent model failure was a harness bug. The em-dash/nimbus incident is the
  sharpest example: a cautionary *example filename* inside the agent system
  prompt was being replayed by weak models as the actual task. Never put a
  replayable, buildable artifact name in a system prompt.
- **Fail-open on every best-effort side-channel** (search, memory, peer
  consult, leader review). Never block the primary path.
- **Bounded retries, usually capped at one.**
- **Ask before committing**, and check `git diff --stat` shows only intended
  files.
