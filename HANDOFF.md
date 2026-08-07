# VibeAI — Handoff Summary

Paste this whole file as the first message in the new chat. It exists purely
to transfer context cheaply — the project itself is the source of truth, this
is a map of where things are and what's mid-flight.

**Repo:** `c:\VS Codes\vibe_ai_v4\vibe_ai`
**Branch:** `feat/website-showcase-chat`
**Git user:** `tamanpro26`

---

## 1. What VibeAI is

A multi-provider, multi-agent AI orchestration system, built entirely on
free-tier LLMs (no paid API keys). A **Manager** routes requests to specialist
**teams** (`brain`, `code`, `design`, `vision`, `router`, `leadership`), which
draft, critique, and (as of this session) execute-and-verify answers before
returning them. Two surfaces sit on top of it:

- **`Neuronova-vibeaiwebsite/`** — a SaaS chat website (React + Vite), Clerk
  auth, deployed to Vercel as `vibeai-showcase`. Cascade of engine tiers:
  `live → manager (Render) → omni (local OmniRoute) → edge (Groq proxy) →
  simulated`.
- **`hardware/`** — a physical ESP32 "school heat-monitor" demo: a DHT11
  sensor triggers the real AI backend, which composes a buzzer/LED alarm
  rhythm live and can push a browser notification.

Real, verified numbers used on the landing page: 38 model registry entries,
10 free-tier providers, 475+ automated tests, $0 paid keys.

---

## 2. Most recent work (this session, in order)

### A. Chat website — complete visual redesign
Two things happened here, in sequence — read both, the second superseded part
of the first:

1. Built a "witnessed record" design direction (bound notebook metaphor:
   numbered entries, countersign stamps, carbon-blue paper). Documented in
   `Neuronova-vibeaiwebsite/CHAT_REDESIGN.md` (history — describes a direction
   that was later replaced).
2. **A design audit of the running app** (computed values measured directly,
   not code-reading) found it still read as "cheap": 48/67 elements at 0
   border-radius, 56/67 at `transition-duration: 0s`, mono font on 17 chrome
   elements, a mint-green border color clashing with the red-orange accent,
   fluid/`calc()`-derived type producing fractional pixel sizes. Rebuilt the
   whole token system on that audit — this is **what's actually live now**.

**Current source of truth:** `Neuronova-vibeaiwebsite/DESIGN.md` (rewritten
from the built code, not from intent — trust this over `CHAT_REDESIGN.md`).

Key files touched: `src/index.css` (full token rewrite — 4 surface layers,
one neutral border family, `--accent` honestly named instead of living in a
variable called `--cyan`, fixed type scale, Framer Motion-ready), `src/chat/
chat.css` (830 lines changed), `ChatApp.jsx`, `Message.jsx`, `SettingsModal.jsx`
(now uses `AnimatePresence` for real exit animations — it used to `return null`
on close, which meant it could never animate out), `Sidebar.jsx` (footer
truncation fix, avatar+menu instead of two competing buttons), new
`ListboxSelect.jsx` (custom accessible combobox, replacing a native `<select>`
that rendered OS chrome).

**Fixed a real a11y bug:** `SettingsModal` referenced `reduceMotion` without
declaring it — `useReducedMotion()` was imported but never called, so it was
always `undefined`/falsy, meaning reduced-motion users got the full animation
regardless of their OS setting.

**Not yet done / explicitly out of scope:** landing page (`src/components/`)
only got a targeted fix (12 hardcoded cyan values pointed at the new accent
token) — it wasn't fully redesigned, that was out of scope. Loading/streaming
states also weren't re-authored in the new vocabulary (error state was).

### B. Backend — put real ground truth into the verification loop
A second, independent critique (of the *AI orchestration* itself, not the
UI) argued the biggest defect was: every "verification" step was one model
scoring another model's text — no compiler, no test runner, no execution
anywhere in the loop. Investigated and found this was **exactly right**:

- `tools/code_executor.py`'s `CodeExecutor`/`CodeVerifiedReasoner` already
  existed, fully built, subprocess-sandboxed — with **zero callers** anywhere
  in the codebase.
- `teams/code.py::_verify` gated generated code on `compile()`, which only
  catches `SyntaxError`. `NameError`, `ImportError`, undefined symbols, bad
  attribute access all sailed through uncaught — exactly the class of bug
  weaker models produce. Actual execution only ran when the model happened to
  emit `>>>` doctests or `assert`, a minority of generated code.

**Fixed:** every extracted Python block is now actually imported in the
sandbox; real tracebacks feed the pre-existing repair pass. `__name__` is
reassigned to a dummy value first, so this is an *import* test, not a *run*
— a script expecting `argv`/stdin or with a long-running `main()` doesn't get
falsely flagged as broken.

**Then found a second, more important hole via live testing** (not
assumption): `manager/claude_manager.py::_try_fast_path` answers requests
classified "simple" with **one direct model call and returns immediately**,
*before* team dispatch — so code from the fast path never reached
`CodeTeam._verify` at all, and "simple" is the common case. Confirmed live
against the deployed Render backend: asked for a snippet calling an undefined
function, got it back unrepaired. Fixed by routing fast-path code output
through the same `CodeTeam._verify`, wrapped so a checker failure degrades
gracefully rather than breaking an otherwise-good answer.

New test: `tests/test_execution_verify.py` (5 tests — confirms a `NameError`
invisible to `compile()` is caught, and confirms a legitimate `__main__` guard
is *not* falsely tripped).

**Commits (pushed to origin):**
```
65720e0 fix: verify code on the manager fast path, which bypassed every check
465b2d8 feat: execute generated code before returning it; premium chat redesign
3aa7209 feat: themeable chat UI, personalized AI, real web search, fire-alert hardware
```

**Explicitly not done yet** (ranked by the critique that prompted this work):
1. Best-of-N candidate selection by *execution* instead of a judge model
   (`teams/code.py::_best_of_n` still picks via `gpt_oss_120b_debug` as judge —
   now cheap to fix since the executor is wired in both other paths).
2. LSP diagnostics (`multilspy` + pyright) for reference/type errors execution
   won't catch.
3. Prune 100+ installed skills to ~15 — every skill's metadata loads into the
   system prompt regardless of use.
4. The critique also argued for cutting the model roster (36 registry
   entries) down to ~4 architecturally-distinct models and dropping
   critique-only stages for weak models — **not yet acted on**, this is a
   product decision (changes what VibeAI claims to be), not a drop-in fix.

### C. Hardware — ESP32 fire-alert demo
Physically wired and working: DHT11 sensor (GPIO27, needs a 10kΩ pull-up to
VCC — was previously miswired to GND, which is what caused a long dead-sensor
debugging session), buzzer (GPIO23) + second buzzer (GPIO21, independently
controllable), 3 LEDs (GPIO4/18/19). AI composes a buzzer+LED alarm rhythm
live per temperature reading (see `core/device_planner.py` — rhythm literally
scales with how hot it is, e.g. calm/steady/urgent bands). Full architecture
+ pin map + wiring diagram: **`hardware/CIRCUIT.md`**.

**Just changed, not yet flashed:** temperature threshold moved from 30°C to
40°C (30 was a demo-sensitivity hack; 40 is a realistic fire threshold — still
well under the DHT11's 50°C rated ceiling). Updated in three places to stay
consistent:
- `hardware/esp32-firmware/src/main.cpp` — `TEMP_THRESHOLD_C = 40.0`
- `core/device_planner.py` — AI's severity bands rescaled to fit the tighter
  40→50°C headroom (was 30–36 / 36–45 / 45+, now 40–44 / 44–47 / 47+)
- `core/device_knowledge.py` — device manifest text updated to match

**BLOCKED:** flashing this to the board failed 3 times in a row just now —
`A serial exception error occurred: Write timeout` on COM4, immediately at
"Connecting...". This is a different failure mode than the session's earlier
recurring issue (that one needed a manual hold-BOOT/tap-EN/release-BOOT
sequence and eventually succeeded); three straight timeouts suggests something
new — check the USB cable/port, or whether another process has COM4 open,
before retrying `pio run --target upload` from
`hardware/esp32-firmware/`.

**Network note:** the ESP32's configured server IP lives in
`hardware/esp32-firmware/src/wifi_secrets.h` (gitignored — real credentials;
template is `wifi_secrets.example.h`). It was JUST corrected this session from
a stale `10.131.91.88` to the laptop's then-current `10.110.58.87` — **the
laptop's IP changes across sessions/networks** (this has caused multiple past
failures), so if hardware testing resumes and the board can't reach the
server, check `ipconfig` first and compare against `SERVER_HOST` in that file.

**Also uncommitted, unrelated to the above:** `tests/test_core_logic.py` has
a 42-line addition (diff not yet reviewed in this handoff — check
`git diff tests/test_core_logic.py` first thing in the new chat).

---

## 3. Uncommitted state right now

```
M api/server.py
M core/device_knowledge.py
M core/device_planner.py
M hardware/esp32-firmware/src/main.cpp
M tests/test_core_logic.py
```

None of this is pushed. The 40°C threshold change (planner/knowledge/firmware)
is coherent and tested-in-principle but **not flashed to real hardware yet**
(see blocker above) and not committed. `api/server.py`'s diff hasn't been
reviewed in this handoff either — check `git diff` on all five before
assuming what's in them.

---

## 4. Working conventions established this session

- **Never commit or push without being asked explicitly.** Multiple commits
  this session only happened after a direct "can you do these things?".
- **Verify live, don't assume.** The fast-path verification hole was found by
  actually calling the deployed Render backend with a deliberately broken
  prompt, not by reading code and guessing. Same pattern throughout: flash →
  check server log / serial monitor → confirm, rather than declare success
  from a compile pass alone.
- **When flashing the ESP32:** `pio run --target upload` from
  `hardware/esp32-firmware/`. The board frequently needs a manual
  hold-BOOT → tap-EN/RESET → release-BOOT sequence timed against the
  "Connecting..." message. Prefer checking the server log for the board's own
  HTTP requests over opening the serial monitor — opening/closing serial
  connections has repeatedly frozen the board mid-session (DTR/RTS reset
  toggling), whereas the server log is a passive, non-invasive check.
- **`impeccable` design-review hook** fires automatically on UI file edits in
  this repo. Two findings on `hardware/demo/index.html` (single-font,
  flat-type-hierarchy, dark-glow) have been repeatedly re-flagged and
  repeatedly confirmed as intentional (it's a deliberately flat local-only
  debug fixture) — don't re-litigate those unless asked.
- User prefers terse, direct answers (caveman-mode is active in the harness
  config) — lead with the answer/action, keep prose minimal, no filler.

---

## 5. Suggested first move in the new chat

Paste this file, then either:
- Resolve the ESP32 flash blocker (check COM4/cable/competing process, retry
  upload), or
- Review `tests/test_core_logic.py`'s uncommitted diff before doing anything
  else with it, or
- Continue down the backend critique's ranked list (best-of-N by execution is
  next and is now cheap).
