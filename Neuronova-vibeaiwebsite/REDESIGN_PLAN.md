# VibeAI Web — Premium Redesign Plan (v2)

**Status:** P0–P3 shipped (2026-07-31).

> ### ⚠ Deployment step required for search
> `api/research.js` needs **`EXA_API_KEY`** in the Vercel project env
> (Settings → Environment Variables), then a redeploy. The key already exists
> in the Python side's `.env` but Vercel cannot see that file. Until it is
> added, the endpoint returns 501 and research silently falls back to
> Wikipedia — which is the pre-existing behaviour, not a regression.


**§9 decision now made:** the `DESIGN.md` serif ban is **theme-scoped**, not
product-wide. Serif is permitted in Studio DISPLAY roles only — never in
Cockpit, never in body copy in either theme. Studio display is
`Instrument Serif`; the selectable reading serif is Georgia, chosen because it
ships with every OS (no extra webfont request) and was designed for screen
reading, where a display face like Instrument Serif turns mushy at body sizes.
 Both themes render from identical
markup; settings shell, theme switch, and appearance preferences are live.
Next up: P2 (typography + Studio polish) and P3 (server plumbing so custom
instructions actually reach the model — the settings UI currently stores them
but nothing consumes them yet).

**Correction to the §2 audit found during P0:** the audit counted radii and
shadows but missed **39 hardcoded `rgba(43,232,255,…)` accent tints** plus hex
literals in `chat.css`. These would have stayed cyan on Studio's warm surface —
a theme switch that silently half-applies. Fixed by exposing raw channel
tokens (`--accent-rgb`, `--mint-rgb`, `--text-rgb`) so one token drives every
alpha, since a hex `--accent` cannot be given an alpha inside `rgba()`.
**Owner decisions:**
- *2026-07-31* — ship *both* design directions as user-switchable themes rather
  than picking one.
- *2026-07-31 (v2)* — add Skills, coding Projects, an options-based question
  UI, a real typography upgrade, and a premium motion layer. Goal is explicitly
  to stand next to Claude/ChatGPT on quality, built our own way.

Reference material: Claude desktop screenshots supplied by the owner (sidebar
with Projects/Artifacts/Customize, settings modal with left-nav + right pane,
profile fields, Appearance/Chat-font/Motion controls, serif home greeting).
Those inform the **Studio** theme and the settings IA — they are a quality bar
to match, not a design to copy.

---

## 1. What we are building

In dependency order:

1. **Theme token layer** — the foundation everything else needs.
2. **Two complete themes** — `Cockpit` (today's HUD, refined) and `Studio`
   (soft premium), switchable at runtime.
3. **Typography upgrade** (§9) — the single highest visual-impact change.
4. **Settings / "Design"** — profile, custom instructions, appearance.
5. **Projects** — containers, including code projects.
6. **Skills** — reusable capability packages the AI team can load.
7. **Artifacts** — generated-output side panel.
8. **Options-based questions** (§10) — the AI asks with clickable choices.
9. **Premium motion layer** (§11).

---

## 2. Honest audit of where the code actually is

Measured, not assumed (2026-07-31):

| Fact | Implication |
| --- | --- |
| Design tokens live in `src/index.css` `:root` — **colors and fonts only** | No radius/shadow/spacing tokens exist. Theming cannot work until these are added. |
| `src/chat/chat.css` is **1703 lines** with **38 hardcoded `border-radius`** and **24 hardcoded `box-shadow`** | Every one must be replaced with a token or it will not respond to a theme switch. Largest single chunk of work. |
| `src/chat/store.js` persists to **localStorage, per user** | Projects/artifacts would be per-browser only and lost on cache clear. See §8.4. |
| `api/chat.js` accepts a `messages` array and a server-side `MODES` whitelist. **No system-prompt handling exists.** | Custom instructions need real server plumbing, not just a UI field. |
| `api/team.js` forwards to the Python Manager, which is **stateless per call** | Project/skill context must be re-sent on every request. |
| `manager.handle_user_request(prompt, extra=None)` already takes an **`extra` dict** | This is the real injection point for Skills and project context — no new transport needed. |
| Python teams already exist: `brain`, `code`, `design`, `vision`, `router`, `leadership` | Skills should *compose* these, not replace them. |
| `DESIGN.md` explicitly bans soft radii, elevation shadows, and serif | Must be rewritten as a two-theme document, with theme-scoped exceptions. |

**The hard truth about dual theming:** this is not a color swap. Cockpit is
dense/mono/4px-radius/glow-based. Studio is airy/serif-display/16px-radius/
shadow-based. They differ in *radius, shadow, spacing, font role, and density*.
Skipping the token layer and only swapping colors produces a broken half-theme.

---

## 3. Foundation — the theme token layer

Extend `:root` in `src/index.css` from colors-only to a full token set, then
override per theme on a `data-theme` attribute on `<html>`.

```css
:root { --dur-fast: .12s; --dur: .2s; --ease: cubic-bezier(.4,0,.2,1); }

[data-theme="cockpit"] {
  --radius-sm: 3px; --radius: 4px; --radius-lg: 6px; --radius-pill: 6px;
  --elev-1: 0 0 12px -4px rgba(43,232,255,.35);   /* glow, not elevation */
  --elev-2: 0 0 20px -6px rgba(43,232,255,.30);
  --space-unit: 4px;
  --font-ui: var(--font-mono);
  --font-display: 'Chakra Petch', sans-serif;
  --text-base: 14px;
}

[data-theme="studio"] {
  --radius-sm: 8px; --radius: 12px; --radius-lg: 18px; --radius-pill: 999px;
  --elev-1: 0 1px 2px rgba(16,14,12,.06), 0 4px 12px rgba(16,14,12,.05);
  --elev-2: 0 2px 4px rgba(16,14,12,.06), 0 12px 32px rgba(16,14,12,.08);
  --space-unit: 6px;
  --font-ui: var(--font-body);
  --font-display: 'Instrument Serif', Georgia, serif;
  --text-base: 16px;
}
```

Required token groups: `--radius-*` (4), `--elev-*` (2), `--space-unit` +
derived scale, `--font-ui`, `--font-display`, `--text-base` + type scale, and
all colors re-declared per theme.

### 3.1 Execution order for the CSS migration

1. Add the token blocks. Change no component yet.
2. Sweep `chat.css` replacing all 38 radii and 24 shadows with tokens.
   **No visual change should result** — Cockpit values *are* the current
   values. This is the checkpoint: if the UI shifts, something mapped wrong.
3. Only then author Studio overrides.

Verify step 2 with a before/after screenshot diff at identical viewport.

---

## 4. Theme A — `Cockpit` (default, refined)

Keep the identity in `DESIGN.md` §1–§8. "Premium" here means *precision*, not
softness — the Linear/Vercel end of technical UI.

- **Type scale discipline** — replace ad-hoc sizes with a 1.2-ratio modular
  scale off `--text-base`. Current sizes drift.
- **Spacing rhythm** — all padding/margins on the `--space-unit` grid.
- **Real loading state** — `DESIGN.md` §4 flags this missing. Build the
  skeletal shimmer + negative-spread glow breathe for "model is thinking".
  No spinners.
- **Focus-visible pass** — consistent keyboard rings using `--cyan-dim`.
- **Message max-width** locked to ~65ch.

## 5. Theme B — `Studio` (soft premium)

| Aspect | Cockpit | Studio |
| --- | --- | --- |
| Surface | `#05070a` void black | `#faf9f7` warm off-white |
| Panel | `#0a1119` | `#ffffff`, hairline `#e8e4de` |
| Text | `#d9f6fb` | `#2c2925` warm near-black |
| Radius | 3–6px | 8–18px |
| Depth | glow (alpha ≤.35) | soft elevation shadows |
| Display font | Chakra Petch | editorial serif |
| UI font | IBM Plex Mono | IBM Plex Sans |
| Density | 8/10 | 5/10 |

**Global constraints that survive both themes:** no `Inter`, no gradient text,
no emoji in UI chrome, no AI-copywriting clichés. Those `DESIGN.md` §8 bans are
about taste, not the cockpit identity.

A `studio-dark` variant is explicitly **out of scope for v1** — two themes done
well beats three done badly.

---

## 6. Settings — the "Design" section

Route `#/settings`, rendered as a modal with left-nav + right pane (matching
the reference IA). This is where the theme switch lives, which is what makes
"Design" a real section rather than a label.

### 6.1 Profile
- Display name
- What should VibeAI call you
- What best describes your work (role) — informs tone
- (read-only) email from Clerk

### 6.2 Personalization
- "What should VibeAI know about you?"
- "How should VibeAI respond?"
- Live character count, hard cap ~1500 chars each to protect token budget.

### 6.3 Appearance
- **Theme:** Cockpit / Studio, applied instantly via
  `document.documentElement.dataset.theme`
- Chat font (see §9)
- Density: comfortable / compact
- Text size: S / M / L (scales `--text-base`)
- **Motion:** full / reduced / off — must also respect
  `prefers-reduced-motion` by default

### 6.4 Server plumbing — the part that is easy to forget

A settings UI that does not reach the model is decoration.

- `api/chat.js` — accept optional `systemPrompt`, prepend as a
  `{ role: 'system' }` message. **Validate and cap length server-side**; never
  trust the client's cap.
- `api/team.js` — the Manager is stateless, so pass the composed profile via
  the existing **`extra` dict** on every request.
- `src/chat/engine.js` — thread the composed prompt through `respondOmni`,
  `respondEdge`, `respondResearch`, `respondTeam`, and both `continue*`
  functions. Missing one leaves an inconsistent persona.

**Composition order:** global custom instructions → project instructions →
active skills. Later entries specialise earlier ones.

---

## 7. Projects (including code projects)

A project is a container: grouped chats + project instructions + enabled
skills + (v2) files.

### 7.1 Data model (`store.js`)

```js
{
  id, name, description,
  instructions,        // project-level system prompt
  skillIds: [],        // enabled skills, see §8
  kind: 'general' | 'code',
  color,               // reuse team accent palette, no new hues
  createdAt, updatedAt,
  chatIds: [],
  files: []            // v2 — see §7.4
}
```

Conversations gain an optional `projectId`. Chats without one stay in the flat
list, so **nothing changes** for a user who never opens Projects.

### 7.2 Code projects
`kind: 'code'` auto-enables the Code skill, routes to the Python `code` team,
and defaults the artifact panel open. It is a preset over the same machinery —
resist building a separate subsystem.

### 7.3 UI
- Sidebar section above chat history, collapsed by default, with counts.
- Project detail: title, instructions editor, skill toggles, chat list,
  "New chat in project".
- Move a chat into a project from its existing context menu.

### 7.4 Knowledge files (deferred)
Real RAG needs chunking, embeddings, and a vector store — `core/collective_memory.py`
already owns this on the Python side. **Do not build a second retrieval system
in the frontend.** v1 ships text instructions only.

---

## 8. Skills

Reusable capability packages the AI team can load, toggleable per project.

### 8.1 What a skill actually is

```js
{
  id, name, icon, description,
  systemPrompt,        // injected instructions
  preferredTeam,       // 'code' | 'brain' | 'design' | 'vision' | null
  suggestedMode        // 'fast' | 'balanced' | 'deep'
}
```

**Critical architectural point:** skills must *compose the existing Python
teams*, not duplicate them. The backend already has `brain`, `code`, `design`,
`vision`, `router`, `leadership`. A skill is a named preset that selects and
primes them via the existing `extra` dict — it is not a new agent runtime.
Building a parallel agent system in the frontend is the main failure mode to
avoid here.

### 8.2 Built-in skills (v1)

| Skill | Team | Purpose |
| --- | --- | --- |
| Code Review | `code` | Review a diff/file for defects and risk |
| Build & Debug | `code` | Write and fix code, artifact-first |
| Research | `brain` | Grounded research with sources (existing keyless RAG) |
| Design Critique | `design` | UI/UX review against the design system |
| Data Analysis | `brain` | Reason over pasted tabular data |
| Explain | `brain` | Teach a concept at a chosen depth |

### 8.3 UI
- `Customize → Skills` in settings: browse, read, enable/disable.
- Per-project skill toggles in the project detail view.
- Active skills shown as removable chips above the composer, so the user can
  always see what is influencing the answer. **Invisible prompt injection is
  the thing to avoid** — if a skill changes behaviour, the UI must say so.

### 8.4 Persistence
Skills are small JSON — localStorage is fine. Artifacts are not (see §12).

---

## 9. Typography upgrade

The single highest visual-impact change, and currently the weakest area.

- **Studio display:** an editorial serif (candidate: `Instrument Serif` —
  free, distinctive, off the Inter/Geist convergence axis) used for the home
  greeting and section headings only.
- **Studio body/UI:** `IBM Plex Sans` — already loaded, off-axis, good.
- **Cockpit:** unchanged (`Chakra Petch` display, `IBM Plex Mono` UI).
- **User-selectable chat font** in Appearance, mirroring the reference's
  "Chat font" control.

**Documented exception required:** `DESIGN.md` §3 bans serif outright. That ban
is correct *for the cockpit dashboard* and must be rewritten as
**theme-scoped** — serif permitted in Studio display roles only, never in
Cockpit, never for body text in either theme.

Also fix the existing modular-scale drift (§4) — most "cheap" feeling comes
from inconsistent sizing, not from the font choice itself.

---

## 10. Options-based questions

Let the AI ask a clarifying question with clickable choices instead of
free-text ping-pong. This is a genuine differentiator and directly matches the
reference product's behaviour.

### 10.1 Contract
The model emits a fenced block the frontend detects:

```json
{ "type": "question", "header": "Auth method",
  "question": "Which approach?",
  "options": [ { "label": "...", "description": "..." } ],
  "multiSelect": false }
```

### 10.2 Rules
- Parse **only** fenced blocks with an exact `type: "question"` — never
  free-text pattern matching, which would misfire on ordinary prose.
- Render as option cards; clicking sends the chosen label back as the user's
  next turn. Always include a free-text escape — the model's options are
  frequently incomplete.
- If parsing fails, fall back to rendering the raw block as normal text.
  A malformed question must never blank the message.
- Cap at 4 options; ignore extras rather than overflowing the layout.

---

## 11. Premium motion layer

Motion is what separates "looks fine in a screenshot" from "feels expensive".

- **Message entry:** 8px rise + fade, `--dur` with the shared easing. Staggered
  only for the first paint of a conversation, never on every token.
- **Streaming:** the established negative-spread glow breathe (Cockpit) /
  a soft caret pulse (Studio). No bouncing dots.
- **Theme switch:** cross-fade surfaces over ~180ms. Instant swaps look broken
  at this scale.
- **Artifact panel:** slide + width transition on the message list, not an
  overlay.
- **Sidebar/modal:** transform + opacity only.

**Hard rules:** animate only `transform`, `opacity`, `box-shadow`,
`border-color`, `background` — never `top/left/width/height`. Everything must
be disabled by `prefers-reduced-motion` and by the Appearance motion setting.

---

## 12. Artifacts

Claude-style side panel for substantial generated output.

### 12.1 Detection
Promote when a response contains a fenced code block that is ≥15 lines or a
renderable type (`html`, `svg`, `jsx`, `mermaid`, `markdown`). Short snippets
stay inline — over-promoting is worse than under-promoting.

### 12.2 Panel
Slides in from the right; the message list narrows rather than being overlaid.
Tabs: **Preview** / **Code**. Copy, download, version history (each
regeneration appends a version). Mobile: full-screen sheet.

### 12.3 Security — non-negotiable

HTML/SVG preview renders **model-generated markup**. It must run sandboxed:

```html
<iframe sandbox="allow-scripts" srcdoc="..."></iframe>
```

- `allow-scripts` **without** `allow-same-origin`. Granting both together is
  equivalent to no sandbox — the frame regains access to the parent origin,
  cookies, and the Clerk session token.
- Never `dangerouslySetInnerHTML` model output into the main document.
- Add a `Content-Security-Policy` to the frame content.

Highest-risk item in the plan. Build it last; test with a deliberately hostile
artifact that tries to read `localStorage` and `document.cookie`.

### 12.4 Persistence reality check
`localStorage` is ~5MB per origin and already holds every conversation.
Artifact bodies will hit `QuotaExceededError` mid-session.
**Use IndexedDB for artifact bodies** (localStorage keeps metadata only).
Backend persistence is correct long-term but needs user-scoped auth on the
Python side.

---

## 13. Phasing

Each phase must leave the app deployable.

| Phase | Scope | Done when |
| --- | --- | --- |
| **P0** ✅ | Token layer + no-op `chat.css` migration (§3) | **Done.** Diff clean (0 non-radius/shadow changes); runtime values identical; all tokens resolve |
| **P1** ✅ | Settings shell + Profile + Personalization + Appearance; theme switch, density, text size, motion (§6) | **Done.** Both themes verified from identical markup; prefs applied pre-paint, no flash |
| **P2** ✅ | Typography (§9): modular scale, Studio serif display, chat-font control | **Done.** 18 ad-hoc sizes → 9-step scale off one ratio; scale rescales with text-size; each theme resolves its own display face |
| **P3** ✅ | Custom instructions + server plumbing (§6.4) + **real web search** | **Done.** Profile reaches all 3 live tiers (edge/omni/manager); `api/research.js` adds keyed Exa search with Wikipedia fallback |
| **P4** | Projects (§7) | Chats group under projects; project instructions reach the model |
| **P5** | Skills (§8) | Enabling a skill measurably changes routing/behaviour, and the UI shows it is active |
| **P6** | Options-based questions (§10) + motion layer (§11) | Malformed blocks degrade gracefully; reduced-motion fully honoured |
| **P7** | Artifacts (§12) incl. sandbox hardening | Hostile-artifact test passes; no quota errors after 50 artifacts |

Rewrite `DESIGN.md` as a two-theme document at the end of **P2**, once real
values exist — writing it earlier documents intentions instead of the artifact.

---

## 14. Open decisions

1. **Theme FOUC.** Theme must be applied before first paint via a small inline
   script in `index.html` reading localStorage. Otherwise every Studio user
   sees a black flash. Decide: inline blocking script (correct) vs. accepting
   the flash (not acceptable).
2. **Default theme for new users.** Cockpit shows the product's real identity;
   Studio is more familiar. Leaning Cockpit as the differentiator.
3. **Studio accent colour.** Needs one restrained accent that is *not* signal
   cyan (cyan on warm off-white reads cheap). Must not become a sixth ad-hoc
   hue. Candidate: a muted terracotta or deep warm neutral.
4. **Team accent colours in Studio.** The six team hues are tuned for black
   and will be too loud on off-white; they need lightness-adjusted per-theme
   variants, not reuse.
5. **Mobile navigation depth.** Sidebar → project → artifact is three levels;
   needs a real model on a phone, not three stacked drawers.
6. **User-authored skills.** v1 ships built-ins only. Letting users write skill
   prompts is a prompt-injection surface worth designing deliberately later.

---

## 15. What this plan deliberately does not do

- No Studio dark variant in v1.
- No frontend RAG / file embeddings (Python side owns retrieval).
- No backend persistence in v1 (IndexedDB instead).
- No new accent hues beyond the documented palette.
- No parallel agent runtime in the frontend — skills compose existing teams.
- No changes to the hardware path: `api/sensor`, the device planner, and the
  ESP32 firmware are untouched by any of this.
