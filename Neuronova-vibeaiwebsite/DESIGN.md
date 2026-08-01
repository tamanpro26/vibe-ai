# Design System: VibeAI chat

Recorded from the built code (2026-08-01). Product truth: `PRODUCT.md`.

Supersedes the "witnessed record" direction in `CHAT_REDESIGN.md` — that world
(carbon-blue page, stamp countersign, ruled grid, bound spine) was built and
then replaced after a design audit of the running app. `CHAT_REDESIGN.md` is
kept as history; **this file is what the code does.**

The bar is Claude / ChatGPT / Perplexity / Raycast, executed straight. Three
rules carry most of it:

- **Restraint** — one accent used twice, one sans, one content column, one
  neutral border family.
- **Physicality** — soft edges, stacked layers, response within 140ms.
- **Air** — the composer gets real padding and real room.

## 1. Surface

Four layers. Depth is stacked surfaces first, shadow second, border last —
border-first depth reads as a wireframe.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `--bg-canvas` | `#0a0a0d` | `#f6f6f7` | app background |
| `--bg-surface` | `#111116` | `#ffffff` | sidebar, panels |
| `--bg-raised` | `#17171e` | `#ffffff` | cards, list items |
| `--bg-overlay` | `#1e1e27` | `#ffffff` | menus, composer |
| `--bg-inset` | `#08080b` | `#f0f0f2` | code blocks, wells |

Themes are `[data-theme="cockpit"]` (dark, default) and `[data-theme="studio"]`
(light). Names are legacy; they are now just dark and light.

## 2. Border

**One neutral family.** `--border-subtle` / `--border-default` /
`--border-strong`, white-alpha in dark and black-alpha in light, so borders
inherit whatever surface is beneath them. Never a hue.

## 3. Accent

`--accent: #e8573d` (dark) / `#d1452b` (light).

**Rule: filled accent appears at most twice per screen, and never as an
outline.** Currently: the send button (filled) and the active mode segment
(subtle tint). An accent-outlined button is the Bootstrap-era tell; one
saturated colour ringing many objects collapses hierarchy.

`--agent-*` hues are functional status only (which specialist answered).
`--success` / `--warning` / `--danger` are states, never decoration.

## 4. Type

- **One sans: `Public Sans`** (`--font-sans`) for everything.
- **`Martian Mono`** (`--font-mono`) only in code blocks, code chrome,
  counters, latency, and file sizes — 9 declarations total. Mono in UI chrome
  reads as an internal dev tool.
- **Fixed steps**, not derived: `--fs-micro` 11 · `--fs-caption` 12 ·
  `--fs-label` 13 · `--fs-body` 15 · `--fs-lg` 17 · `--fs-title` 20 ·
  `--fs-h2` 26 · `--fs-hero` 38. Fluid/`calc()` type produced fractional
  computed sizes and blurred the hierarchy.
- **Weight stops at 600.** 700 reads chunky at display sizes.
- Hero: 38px / 600 / `-0.022em`.

## 5. Radius

`--r-xs` 4 · `--r-sm` 6 · `--r-md` 10 · `--r-lg` 14 · `--r-xl` 20 ·
`--r-2xl` 28 · `--r-full` 999.

Buttons and inputs 10, cards 14, composer 20, avatars and send button full.
Sharp corners plus near-black plus a saturated accent is the terminal-template
signature.

## 6. Elevation

`--e-1`…`--e-4`, each two stacked shadows with offset and blur. A single
shadow reads flat. `--e-focus` is the one focus treatment everywhere; never
`outline: none` without a replacement.

## 7. Motion

Durations `--dur-instant` 80 · `--dur-fast` 140 · `--dur-base` 200 ·
`--dur-slow` 360. Curves `--ease-out` (exponential deceleration) and
`--ease-in-out`. No spring/overshoot curve — overshoot reads dated here.

- A global rule in `index.css` gives every button, link, input and `[role]`
  element a 140ms transition. Absence of motion was the loudest cheap signal;
  tokens that never fire are worse than none.
- Framer Motion (`motion/react`) owns choreography: staggered mount (60ms
  apart, 8px rise) via the shared `RISE` variant, `AnimatePresence` on the
  settings modal and account menu so they have real **exit** animations, hover
  lift on cards.
- Everything respects `prefers-reduced-motion` and the Appearance motion
  control (full / reduced / off).

## 8. Layout

One spine: `--col-content: 720px` for hero, cards, messages, and composer.
Sidebar `--sidebar-w: 264px`. Spacing on the `--sp-*` 4px scale.

Below 860px the sidebar goes off-canvas with a scrim.

## 9. Components

- **Composer** — the heaviest object on the page: `--bg-overlay`, `--r-xl`,
  `--e-3`, 56px min-height, 16px internal padding, filled circular send.
- **`ListboxSelect`** — custom combobox replacing a native `<select>`, which
  renders OS chrome and is the most recognizable "not a real product" tell.
  Full keyboard nav, `aria-activedescendant`, focus return on close.
- **Engine badge** — a quiet chip with a status dot. Caps + mono + a saturated
  outline read as an error state rather than information.
- **Suggestion cards** — SVG icons (never emoji: they render per-OS, cannot be
  coloured or sized), left-aligned, two lines each.
- **Sidebar footer** — one avatar row; Settings and Log out behind a `⋯` menu.
  Two full buttons plus avatar plus two text lines truncated the name to `TA…`.
- Header controls are all **32px** with matching borders and radii.

## 10. Anti-patterns

- No accent-outlined buttons; no accent on more than two objects per screen.
- No mono outside code, counters, and measurements.
- No emoji as icons. No centred text in left-aligned cards.
- No `calc()`-derived or fluid type in app chrome.
- No zero-offset coloured halos; shadows carry offset and blur.
- No hue in border tokens.
- No `Inter` — the most saturated face in this category.
- No `outline: none` without a replacement focus treatment.

## 11. Known gaps

- Error, loading, and streaming states are not re-authored in this vocabulary.
- The landing route (`src/components/`) still uses the older styling; only
  `src/chat/` was in scope.
- The chat sits behind Clerk auth, so visual iteration needs either a signed-in
  session or a throwaway harness page that links `index.css` + `chat.css` and
  renders the real markup.
- `--radius-*`, `--glow-*`, `--fs-1..9`, `--cyan`, `--line` and friends survive
  as **compatibility aliases** mapping to the honest names, so `chat.css` and
  the landing route keep resolving. New code uses the real names.
