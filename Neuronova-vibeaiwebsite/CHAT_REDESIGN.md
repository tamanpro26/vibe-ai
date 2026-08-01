# Chat Redesign — Direction

**Surface:** the VibeAI chat application (`src/chat/`)
**Mode:** Operate — the visitor comes to complete work, over long sessions.
Expression may never obscure the task, state, or a familiar affordance.
**Seed key:** 48256297 · assigned index 6 of the grounded list below.
**Status:** direction locked, not yet built.

---

## 1. Why the current design reads as cheap

This is the useful finding, and it is not "not enough animation."

The AI-chat category reliably ships one look: **near-black canvas, a single
neon accent, glowing edges, mono labels, a centered composer.** Its predictable
opposite is **warm cream ground, high-contrast serif display, terracotta
accent, airy spacing.**

The current product ships *both*. Cockpit is the category default. Studio is
the category default's mirror. Neither is a point of view — they are the two
looks anyone would guess from the phrase "AI chat app," which is exactly why
polish keeps failing to fix the feeling. **You cannot polish your way out of a
default.** Missing motion is a real defect, but it is the symptom; the absence
of an idea is the cause.

Both are therefore treated here as evidence of what the product is, and as
anti-reference for what it becomes.

---

## 2. Derivation

**Mechanism, in one sentence:** a Manager routes your request to specialist
agent teams that draft, critique, and verify an answer before it reaches you —
running entirely on free infrastructure.

**The audience's real scene:** a professional at a desk under a task lamp,
hours at a time, this window beside an editor or a document, several things in
flight.

**The rut, named so no candidate is spent on it:** mission control / HUD. It is
also the literal reading of "orchestration," so it gets exactly one slot and
sits last.

Seven systems from the audience's world — the world of *coordinated expert
work*, ordered by resonance, spanning paper, performance, and drafting
families rather than seven flavors of software chrome:

1. **Newsroom copy desk** — wire copy, slugs, bylines, the sub-editor's marks.
2. **ISO technical documentation sheet** — title block, revision table,
   tolerances, part numbering.
3. **Conductor's full score** — every section's voice stacked and legible at
   once, rehearsal marks.
4. **Air-traffic progress strips** — one strip per task in flight, annotated
   and handed between specialists.
5. **Legal case record** — exhibits, citation apparatus, sworn and sealed.
6. **The bound, witnessed laboratory record** ← **assigned**
7. Mission-control HUD *(the rut, held last)*.

**Why 6 survives on its own merits, not just by the roll:** a witnessed lab
notebook is the only candidate whose native grammar already contains the
product's actual mechanism. Entries are dated and numbered. Work is signed by
whoever did it. A *second party countersigns* to attest it was checked. Results
are tipped in as physical evidence. Corrections are struck through, never
erased, so the record stays honest.

That is not a metaphor laid over the product. It is what the Manager, the
specialist teams, the verifier pass, cited sources, and the truncation notice
already do.

---

## 3. Direction contract

> **THESIS** — Every answer is an entry in a witnessed working record: dated,
> numbered, attributed to the specialists who produced it, and countersigned by
> the verifier that checked it. It refuses the category's floating dark canvas
> with a neon accent, and equally refuses the cream editorial page.
>
> **OWN-WORLD** — A lit working page on a dark desk. Ground is
> carbon-duplicate blue, not paper cream; rules are a fine cyan-green grid;
> covers and chrome are oxblood buckram and black cloth tape; the single
> committed accent is stamp red, reserved for attestation and nothing else.
> Type is a records workhorse (Public Sans) with a ledger mono (Martian Mono)
> for entry numbers, timestamps, and attribution. Components are strips,
> plates, stamps, tipped-in specimens, and struck-through corrections.
>
> **STORY** — The visitor understands that specialists worked and someone
> checked it, believes the result is attributable rather than conjured, and
> continues working inside a record that accumulates instead of scrolling away.
>
> **FIRST VIEWPORT** — The desk is dark and quiet. The record page sits lit and
> slightly inset, occupying the center two-thirds, its grid faintly visible.
> The left edge is the bound spine: cloth tape, the index of past entries, and
> the section tabs. The composer is the ruled entry line at the foot of the
> page — always the same place, the way a notebook's next line always is.
> Nothing floats; everything is on or in the page.
>
> **FORM** — The bound witnessed laboratory record. Position 6 of 7 on the
> ordered grounded list, assigned by seed 48256297. Staging: the lit page on a
> dark desk, single-page working view.

---

## 4. Component translation

Every atom is rebuilt in this vocabulary. A stock component inside a committed
form is the lapse to avoid.

| Today | Becomes |
| --- | --- |
| Chat bubbles | **Entries** — ruled blocks with an entry number, timestamp, and attribution line; no bubbles |
| Assistant avatar | **Attribution slug** — which team drafted it, set in ledger mono |
| Engine badge | **Countersign stamp** — the verifier's attestation, in stamp red, the one place that color appears |
| Sidebar | **The spine and index** — cloth tape edge, entries indexed by date |
| Composer | **The next ruled line** at the foot of the page, with the rule visible |
| Mode selector | **Nib / procedure selector** — Fast, Balanced, Deep as a graded instrument |
| Research sources | **Tipped-in specimens** — citations pasted onto the page with visible mounting |
| Truncation notice | **"entry continues overleaf"** — a real page-turn affordance |
| Settings modal | **The record's front matter** — index tabs down the left, plate on the right |
| Empty state | **A fresh page** — grid, date, entry 001, nothing else |

---

## 5. Motion — the form's native motion, orchestrated once

The critique was specifically that things simply appear. The remedy is not
scattered hover effects; it is giving the form the motion it has in life.

A page and a stamp move in exactly two ways, and everything derives from them:

- **Settle** — paper coming to rest. Entrance for entries and panels: a short
  rise with a slight overshoot damping out, opacity resolving *before* travel
  finishes so text is readable while it settles.
- **Impress** — a stamp striking. A fast, hard scale-down onto the surface with
  no bounce, for attestation and commit actions.

Applied:

- **Settings** opens as front matter being turned to: the plate settles in from
  the spine side while the desk behind dims and recedes slightly. Closing
  reverses it. It never simply appears, and it never fades in place.
- **New entry** settles onto the page; the entry number sets first, the body
  resolves after.
- **Streaming** is the nib working — a fine ruled cursor advancing on the line,
  not a spinner and not bouncing dots.
- **Countersign** impresses when the verifier confirms — the single most
  satisfying moment in the interface, and the only one that uses stamp red.
- **Theme change** cross-fades the desk and the page separately, page last.

Bound by `prefers-reduced-motion` and the existing Appearance motion control,
both of which must keep working.

---

## 6. Constraints this direction must respect

Product truth is preserved; only the visual world is replaced.

- Both themes keep working. They become **Desk Lamp** (dark desk, lit page) and
  **Daylight** (the same record under window light) — two lighting conditions
  on one world, not two unrelated identities. This is a real improvement:
  today's two themes share no idea.
- Engine tier, reasoning modes, research with sources, truncation recovery,
  attachments, history, personalization, and Clerk auth all stay.
- The existing token layer (`--radius-*`, `--glow-*`, `--fs-1..9`,
  `--space-unit`, font roles) is the delivery mechanism — token *values* are
  replaced, the architecture is kept.
- Density stays high. This is an Operate surface for long sessions; the
  record's grid is what makes density legible rather than noisy.

---

## 7. Honest risks

1. **Skeuomorphism.** A notebook can slide into fake paper texture and
   drop-shadowed leather. The defense: this is a *record*, expressed through
   structure — grid, numbering, attribution, attestation — not through
   photographic materials. No paper photo textures, no page-curl.
2. **The cream trap.** "Notebook" pulls hard toward cream paper and a serif,
   which is the exact default this direction exists to escape. Ground is
   carbon-duplicate blue; the record face is a records workhorse, not an
   editorial serif.
3. **Legibility of a colored ground.** A saturated ground must still carry
   long-form reading at WCAG AA. The lit page is a *light* value of the blue,
   with the saturation living in the desk, spine, and rules.
4. **Novelty tax.** Users know chat UIs. Entries must still read top-to-bottom,
   the composer must stay where a composer belongs, and every affordance must
   remain obvious on first contact.

---

## 8. Build order

1. Token layer: replace values for the new world in both lighting conditions.
2. Shell: desk, page, spine, index.
3. Entries: numbering, attribution, countersign.
4. Composer as the ruled entry line.
5. Motion system: `settle` and `impress`, then everything derived from them.
6. Settings as front matter.
7. Inspect desktop and mobile in one batched round, fix in one batch, confirm.

`DESIGN.md` is rewritten at the end, from the built world — not before, or it
becomes a rulebook defended against reality.
