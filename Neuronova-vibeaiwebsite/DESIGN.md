# Design System: VibeAI / Neuronova Chat

## 1. Visual Theme & Atmosphere

A dense, confident cockpit-terminal interface for a multi-provider AI
orchestration system — the visual language of a HUD monitoring several
autonomous agents at once, not a soft consumer chat app. The atmosphere is
technical and precise: near-black surfaces, a single cyan signal color for
system-level activity, and per-team accent colors used strictly as functional
status indicators (which specialist model is currently active), never as
decoration.

**Density:** 8/10 — Cockpit Dense. Multiple structural regions visible at
once (sidebar, message list, composer, model-activity indicators); this is
correct for the product and must not be softened toward "Daily App" airiness.

**Variance:** 5/10 — Offset Asymmetric, but restrained. The layout itself
(sidebar + main pane) is a stable, symmetric two-column shell — asymmetry
belongs inside content (message alignment, inline status glows), not in the
shell structure. Do not force a landing-page-style asymmetric hero onto this
app shell; it is a working tool, not a marketing page.

**Motion:** 4/10 — Fluid CSS, not Cinematic Choreography. Existing
transitions are simple 0.2s ease on background/box-shadow/color — appropriate
for a tool used for hours at a time, where restrained, fast, purposeful
motion beats large orchestrated reveals. Glow pulses on active/focus states
are the signature motion, not scroll-triggered staging.

## 2. Color Palette & Roles

- **Void Black** (`#05070a`) — Primary background surface (`--bg`). Not pure
  black; carries a faint blue undertone consistent with the HUD identity.
- **Panel Black** (`#070c12`) — Secondary surface, sidebar background
  (`--bg2`).
- **Recessed Panel** (`#0a1119`) — Tertiary surface for inset panels/inputs
  (`--bg-panel`).
- **Signal Cyan** (`#2be8ff`) — THE single system accent (`--cyan`). Used for
  primary actions (new chat), active/hover states, focus rings, and the
  "manager" team's identity color. Saturation and glow intensity are already
  restrained (`box-shadow` alphas of 0.12–0.35) — never increase to a full
  neon bloom.
- **Dim Cyan** (`#17747f`) — Cyan's low-emphasis sibling for borders and
  secondary focus states (`--cyan-dim`).
- **Structural Line** (`rgba(43,232,255,0.12)`) — Hairline borders throughout
  (`--line`); a tinted line, not flat gray, keeps every seam consistent with
  the cyan HUD identity even at 1px.
- **Ink Text** (`#d9f6fb`) — Primary text (`--text`), a cyan-tinted
  near-white, not pure `#ffffff` — keeps text from fighting the accent.
- **Dim Text** (`#8fb3ba`) / **Faint Text** (`#5d7f88`) — Secondary and
  tertiary text hierarchy (`--text-dim`, `--text-faint`).

**Functional team-identity accents** (a deliberate, documented exception to
"max 1 accent" — these are status indicators in a multi-agent dashboard, not
decorative brand color, and must stay distinguishable from each other and
from Signal Cyan):
- **Manager** — Signal Cyan `#2be8ff` (shares the system accent — the
  manager IS the default/orchestrating voice).
- **Code team** — Ember Orange `#ff7a45`.
- **Brain team** — Warn Amber `#ffdd57`.
- **Vision team** — Soft Violet `#b98bff`.
- **Design team** — Signal Rose `#ff3f8f`.
- **Router team** — Steel Blue `#4d9dff`.
- **Amber** `#ffb020`, **Rose** `#ff3f7f`, **Mint** `#7dffb0` — reserved
  system-status hues (warning, error, success) — never repurposed as
  decorative accents.

Constraints that still apply on top of this real palette: no additional
accent hues beyond the ones listed above, no gradient text, no purple/blue
neon glow effects with alpha above ~0.35, never introduce pure `#000000` or
pure `#ffffff`.

## 3. Typography Rules

- **Display:** `Chakra Petch` (`--font-display`) — a geometric, technical
  display face already in use for brand mark, avatars, and headings. Keep
  tracking tight-to-normal; this face reads as confident at small sizes, so
  resist scaling it up for drama.
- **Body:** `IBM Plex Sans` (`--font-body`) — relaxed leading for message
  content, max ~65 characters per line inside `.msg-content`.
- **Mono:** `IBM Plex Mono` (`--font-mono`) — used pervasively: labels,
  buttons, sidebar chrome, role tags, timestamps, code blocks. This product
  is already correctly "high-density override" per the base ruleset (density
  8/10 → numbers and structural chrome in mono by default).
- **Already compliant, keep enforcing:** no `Inter` anywhere in this stack;
  no generic serif anywhere (this is a software dashboard — serif stays
  banned outright, no editorial-serif exception needed here).

## 4. Component Stylings

- **Buttons** (`.newchat-btn` and similar): flat fill at low alpha
  (`rgba(43,232,255,0.06)`) with a 1px tinted border, hover raises fill alpha
  and adds a soft directional glow (`box-shadow: 0 0 14px rgba(43,232,255,0.2)`).
  No outer neon bloom beyond this intensity. No custom cursors.
- **Border radius:** tight — 3–6px throughout (`.chat-item`, `.msg-avatar`,
  code chips, buttons). This is intentional for the cockpit-terminal
  identity — do NOT widen to generous 2xl/rounded-2.5rem "premium soft"
  radii; that softness belongs to a different product's atmosphere, not this
  one.
- **Cards:** used sparingly (message groups, sidebar items) with hover/active
  states signaled by a tinted background wash (`rgba(43,232,255,0.07)`) and
  an outline, not a drop shadow — shadows here are reserved for glow-style
  emphasis (avatars, active buttons), not elevation.
- **Avatars** (`.msg-avatar`): square-ish with tight radius, glow via
  `box-shadow: 0 0 12px -4px var(--cyan)` for the AI avatar — this negative-
  spread glow technique (tight, inset-feeling glow rather than a broad halo)
  is the signature "active/alive" cue and should be reused for any new
  status-bearing element (e.g., a "model is thinking" indicator).
- **Inputs:** inset panel background (`--bg-panel`), tinted border, focus
  state switches border to `--cyan-dim` — label-above pattern where labels
  exist, mono font for placeholder/chrome text.
- **Loading states:** none currently implemented in chat.css — when added,
  use a skeletal shimmer matching real layout dimensions in the panel-black
  tones, never a generic circular spinner; this keeps loading states
  consistent with the "instrument panel" identity rather than looking
  bolted-on.

## 5. Layout Principles

- Two-column app shell: fixed 280px sidebar + flexible main pane, `100dvh`
  height (already correctly using `dvh` over `vh` — keep this, it's the
  correct choice for mobile Safari).
- This is an application shell, not a marketing page — the "no centered
  hero," "no 3-equal-cards" landing-page rules do not apply here; the
  relevant discipline instead is: no new absolute-positioned overlays that
  break the two-column grid, no layout regressions on narrow viewports.
- Structural seams (sidebar border, item dividers) are always the tinted
  `--line`/`--line-soft` tokens, never a flat neutral gray — this is what
  keeps every added component feeling native to the system instead of
  bolted-on.

## 6. Responsive Rules

- Sidebar (280px fixed) already collapses correctly below 860px: fixed
  off-canvas panel (`transform: translateX(-100%)` → `.is-open` slides it
  in), a dimming scrim, and a hamburger toggle (`src/chat/chat.css:1010`).
  Keep this exact pattern for any new sidebar content — don't reintroduce a
  two-fixed-column layout on narrow viewports.
- No horizontal scroll on the message list or composer at any width.
- Touch targets (chat-item rows, buttons) minimum 44px tall on mobile even
  though desktop density can be tighter.
- Message content font-size floor: 14px real body text, never smaller, even
  at high density.

## 7. Motion & Interaction

- Default transition: `0.2s ease` on `background`, `box-shadow`,
  `border-color`, `color` — already established, keep as the baseline for
  any new interactive element rather than introducing a different easing
  curve.
- Glow-on-hover/focus (tinted `box-shadow`, alpha 0.12–0.35) is the
  system's ONE signature micro-interaction. Reuse it; do not invent a second
  competing motion language (no bounce, no scale-pop, no confetti).
- Any "AI is working" / streaming-response indicator should use the same
  negative-spread glow pulse already established on the AI avatar
  (`box-shadow: 0 0 12px -4px var(--cyan)`), animated as a slow opacity/glow
  breathe — not a spinner, not dots-bouncing.
- Animate only `transform`, `opacity`, `box-shadow`, `border-color`,
  `background` (all already GPU/compositor-friendly here) — never `top`,
  `left`, `width`, `height`.

## 8. Anti-Patterns (Banned)

- No `Inter` (already avoided — keep it that way).
- No generic serif anywhere in this dashboard context.
- No pure `#000000` or pure `#ffffff` (already avoided via `--bg`/`--text`).
- No accent hues beyond the documented palette in §2 — adding an ad-hoc
  purple/pink/green "just for this one button" breaks the team-color
  semantics the whole app relies on to signal which agent is active.
- No neon bloom beyond the established glow-alpha range (~0.12–0.35).
- No generous "premium soft" border radii (2xl/2.5rem) — contradicts the
  cockpit-terminal identity; stay at 3–6px.
- No drop-shadow-style elevation cards — this system signals hierarchy via
  tinted backgrounds/outlines/glow, not shadow depth.
- No emojis in UI chrome (mono-font status text and iconography only).
- No AI copywriting clichés ("Elevate", "Seamless", "Unleash", "Next-Gen")
  in any UI copy, empty states, or placeholder text.
- No generic circular spinners for loading states.
- No filler UX text ("Scroll to explore", bouncing chevrons) — not
  applicable to an app shell, but stays banned if any marketing-adjacent
  page is ever added alongside it.
