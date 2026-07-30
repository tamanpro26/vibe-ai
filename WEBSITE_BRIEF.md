# VibeAI — Website Request & Project Brief

*Prepared for the developer building the website. All design, visual, and
implementation decisions are yours to make — this document exists purely to
give you accurate context on what VibeAI is and what the site needs to
communicate.*

---

## 1. One-line summary

**VibeAI** is a multi-provider, multi-agent AI orchestration system — an
autonomous coding agent plus a multi-model reasoning pipeline, built entirely
on free-tier LLMs with automatic cross-provider failover.

## 2. The problem it solves

Every strong coding/reasoning AI assistant today is built on one paid model
from one vendor. If that vendor rate-limits you, goes down, or you simply
can't afford the API bill, you're stuck. VibeAI's premise: orchestrate many
*free-tier* models from different providers — so no single quota, outage, or
cost is a blocker — and combine them with structured reasoning (planning,
critique, verification) to get reliable results without a paid API key.

It's a solo-built engineering project, not a funded product — the interesting
part is the architecture and the engineering discipline behind it, not a
commercial pitch.

## 3. What it actually does (verified, current numbers)

- **38 model registry entries** across **10 free-tier providers**: Google
  (Gemini), Groq, Cerebras, OpenRouter, NVIDIA NIM, Mistral, Z.AI, Pollinations,
  Ollama, plus optional Anthropic. Entries are (model × provider × role) slots,
  so the same strong model can serve multiple roles for redundancy.
- **An autonomous coding agent** (`core/agent_loop.py`) — takes a task in plain
  English, writes/edits real files, runs shell commands, verifies its own
  output (a deterministic checker for broken imports, placeholder stubs, dead
  images, unstyled CSS), and self-corrects via a reflexion critic before
  reporting done.
- **A "Free Manager Council"** — a 5-stage Plan → Draft → Critique → Refine →
  Synthesize pipeline where multiple free models collaborate on a single
  answer, instead of relying on one model's first attempt.
- **Team hierarchy** — the system is organized into specialist teams (code,
  brain/reasoning, vision, design, routing), each with its own "Leader" review
  step, and an on-demand CEO-style report that summarizes whether the overall
  system is performing well, generated from real operational logs.
- **Resilience layer** — a circuit breaker that tracks per-provider rate
  limits and outages and automatically fails over to the next available
  model, so a single quota hit doesn't stop the system.
- **Two interfaces**: a polished terminal UI (Rich-based CLI) for interactive
  use, and a REST/WebSocket API server (FastAPI) for programmatic access.
- **439 automated tests**, all offline/deterministic — the engineering is
  tested, not just demoed.

## 4. Who the site is for

Best framed as a **project showcase**, not a SaaS landing page: developers,
recruiters, hackathon/college evaluators, or other engineers who want to
understand what was built and why it's technically interesting. There's no
pricing, signup, or commercial angle — *(flag this assumption to Taman if you
were expecting a product-style site instead; easy to redirect either way)*.

## 5. What we need from the site

A single well-designed site (page count and structure fully up to you) that
communicates sections 2–3 above clearly to a technical visitor. As a starting
suggestion only — reshape freely:

- **Hero** — name, one-line pitch, the "why" (free-tier orchestration, no
  single point of failure)
- **How it works** — the architecture at a glance (agent loop, Council,
  provider failover)
- **Features / capabilities** — the bullets in §3, presented visually rather
  than as a wall of text
- **Tech stack / engineering depth** — for the technically-curious visitor
  (Python, the provider list, test count, verifier battery)
- **Contact / links** — GitHub repo link if Taman wants the code linked
  (ask him directly — not included here), contact info below

## 6. Reference material available (optional, not prescriptive)

- `VibeAI_3D_Architecture_Demo.html` in the project root is an existing
  interactive Three.js visualization of the system's architecture, built for
  a college presentation. It already establishes a tagline style —
  **"VibeAI — Multi-Provider Multi-Agent Orchestration Core"** — and a
  techy/HUD visual language. You're welcome to use it as inspiration, embed
  it, or ignore it entirely and design something completely different.

## 7. Things to know before building

- **The codebase is proprietary** ("All rights reserved," per `LICENSE`) —
  the site should not publish source code or imply it's open source unless
  Taman says otherwise.
- **No existing logo or brand assets** — visual identity is a green field.
- If you want the site to demo the system **live** (not just describe it),
  know that the real API is a code-execution surface by design (the agent
  runs shell commands and writes files) — it binds to localhost only by
  default and isn't meant to be exposed publicly without real hardening.
  Treat any "live demo" request as a separate conversation with Taman rather
  than assuming it's in scope.

## 8. Contact

Taman Roy Chowdhury — tamanpro26@gmail.com

Reach out with any questions on scope, content, or anything unclear above —
happy to clarify before you start building.
