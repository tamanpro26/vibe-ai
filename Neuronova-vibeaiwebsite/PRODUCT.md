# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers and other working professionals, using this as a SaaS product in a
browser. Confirmed 2026-07-31: this is not a demo or a portfolio piece — it is
intended as a real product for people doing real work, across professions, not
only engineers.

A secondary evaluation audience exists (exhibition judges assessing the
engineering), but the design authority is the working professional.

## Product Purpose

VibeAI is a multi-provider, multi-agent AI orchestration system with a chat
interface. A user asks for something; a Manager routes it to specialist agent
teams, which produce, critique, and verify the answer before it is returned.

Success is a user getting work done through the agent team, over long sessions,
without needing to know which provider or model served any given turn.

## Positioning

**A real multi-agent team, running entirely on free-tier LLMs.** Confirmed as
the main product. Not a single model behind a chat box: a Manager plus 5
specialist teams (brain, code, design, vision, router), each signed off by a
per-team Leadership review pass, with cross-provider failover, structured
critique, and a verifier pass.

Physical hardware control (an ESP32 fire-alert system where the AI composes
real alarm behaviour from sensor data) is confirmed as an **application** of
the agent team, not the core product. It is proof the orchestration does real
work in the world — it is not the thing being sold.

## Operating Context

- Browser SaaS, authenticated. Sessions are long and work-oriented, not
  one-off visits.
- Users arrive with a task, not to be marketed to. The chat surface is where
  the product is used, so it is an Operate surface, not a Persuade one.
- The system degrades across engine tiers depending on what is reachable
  (real Manager backend → cloud proxy → local gateway), and which tier
  answered is meaningful information to the user.

## Capabilities and Constraints

Confirmed from the shipped code:

- **Engine tiers:** live local → Manager/Council backend (`/api/team`) →
  cloud proxy (`/api/chat`) → local OmniRoute gateway → simulated fallback.
  The active tier is surfaced to the user.
- **Reasoning modes:** Fast / Balanced / Deep, mapped server-side to a model
  whitelist (clients never choose the model or token budget).
- **Research:** keyed web search via Exa (`/api/research`) with a keyless
  Wikipedia fallback; answers are grounded in retrieved sources and cite them.
- **Truncation recovery:** replies cut off by a token ceiling are detected
  (`finish_reason: length`) and can be continued rather than silently
  presented as complete.
- **Personalization:** profile and custom instructions are composed into a
  system prompt and reach every live engine tier.
- **Appearance:** two complete themes plus density, text size, motion, and
  chat-font preferences, applied before first paint.
- **Auth:** Clerk. Session tokens gate every key-holding endpoint.
- **Attachments:** file drag-and-drop into the composer.
- **Persistence:** conversations in localStorage, per user (~5MB origin
  limit is a real ceiling for future artifact storage).
- **Constraint — free-first default:** the automatic cascade is designed to
  operate without paid provider keys. Optional premium and manually selected
  routes exist in the registry, but are not required for the standard path.
- **Constraint — Manager backend is stateless per call:** it generates a
  fresh session internally and retains nothing, so any per-user or
  per-project context must be re-sent on every request.

## Brand Commitments

- Name: **VibeAI** (repository/site also carries "Neuronova").
- Confirmed binding: the interface must communicate that a real agent *team*
  answered, not a single model. This is the product's core claim and the
  design must not obscure it.

## Evidence on Hand

Figures verified against the current repository on 2026-08-06:

- 39 model registry slots
- 11 provider identifiers (including local/gateway and optional routes)
- 502 automated tests collected by pytest
- no paid API key required for the standard free-first path

Also real: a working ESP32 fire-alert build where the AI composes alarm
rhythms from live sensor readings (`hardware/CIRCUIT.md`).

**No** customer testimonials, case studies, pricing, or usage numbers exist.
Future work must not fabricate them.

## Product Principles

1. **The team is the product.** Every surface should make it evident that
   specialist agents collaborated on an answer.
2. **Honest about what answered.** Engine tier, mode, sources, and truncation
   are shown rather than hidden; the user is never misled about how a result
   was produced.
3. **Built for hours, not for a first impression.** Density and efficiency
   serve long working sessions.
4. **Grounded over confident.** Retrieved sources are cited; fabricated
   specifics are a defect, not a style.
5. **Constraint as identity.** Running a real multi-agent system on free
   infrastructure is the achievement — the design should feel engineered, not
   expensively decorated.

## Accessibility & Inclusion

No product-specific standard has been established. Existing implementation
respects `prefers-reduced-motion` and offers an explicit motion control, which
future work must preserve.
