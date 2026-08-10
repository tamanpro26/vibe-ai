# VibeAI Website Improvement Ledger

This ledger records the evidence, decisions, and verification for the autonomous website improvement loop. Scores are intentionally conservative and use the weighting defined in the improvement brief.

## Baseline — 2026-08-06

### Product understanding

- Primary users: developers and technical teams evaluating a multi-provider AI workspace or autonomous coding workflow.
- Primary conversion: open the authenticated VibeAI workspace.
- Secondary conversions: understand the orchestration architecture, inspect product proof, and contact the builder.
- Five-second message required: VibeAI plans, routes, reviews, refines, verifies, and completes complex work across multiple models and tools.
- Verified differentiators: multi-provider routing and fallback, a five-stage Free Manager Council, specialist teams, local workspace execution, deterministic verification, live-search context, and an authenticated web workspace.
- Claims to avoid: complete sandboxing, enterprise-grade security, universal zero-cost usage, a public coding-agent demo, or provider/test counts not derived from the current repository.

### Baseline state

- Overall weighted score: **6.8 / 10**
- Build: PASS (`npm run build`), with a 675.01 kB initial JavaScript chunk warning.
- Lint: PASS with 2 warnings (`auth.jsx` hot-reload export shape and `RegistryPanel.jsx` memo dependency).
- Tests: 502 Python tests collected from the repository virtual environment; full run pending.
- Browser console: no critical errors; one expected Clerk development-key warning in local development.
- Responsive state: no horizontal overflow at 1440 px or 390 px; section navigation disappears below 960 px with no mobile replacement.
- Accessibility baseline: semantic sections and reduced-motion support are present; mobile navigation, route-loading feedback, and interactive proof still need work.
- Accuracy finding: homepage claims 38 registry entries, 10 providers, and 475 tests. Current code exposes 39 registry entries and 11 provider routes; pytest collects 502 tests.

### Existing strengths

- The signal-routing canvas demonstrates failover rather than serving as unrelated decoration.
- The landing page has a coherent, distinctive technical visual language and useful reduced-motion handling.
- The product has honest simulated-feed labeling and a real authenticated application behind the primary CTA.
- The current sections map to actual code paths: council, circuit breaker, teams, agent loop, and API registry.

### Baseline category scores

| Category | Score | Evidence |
| --- | ---: | --- |
| Messaging | 6.2 | Strong technical detail, but the hero leads with project provenance and jargon rather than the user outcome. |
| Visual design | 7.7 | Cohesive composition and hierarchy; oversized wordmark and dense mono styling skew toward a generic AI-engineering showcase. |
| Motion | 7.8 | Motion explains routing and respects reduced motion; some timed loops do not pause outside the viewport. |
| UX | 6.4 | Primary CTA is clear, but mobile loses section discovery and the landing/product transition has no loading feedback. |
| Trust | 6.3 | Honest simulation label and local-only warning; counts are stale and security/control explanation is incomplete. |
| Engineering | 7.0 | Clean production build and reusable components; lint warnings and route coupling remain. |
| Performance | 5.9 | No heavy media, but the public landing eagerly loads Clerk and the entire authenticated workspace in a 675 kB chunk. |
| Accessibility | 7.3 | Semantic structure, labels, focus tokens, and reduced motion exist; mobile nav and several animated status regions need improvement. |

Weighted score: `(6.2×.15) + (7.7×.15) + (7.8×.10) + (6.4×.15) + (6.3×.10) + (7.0×.20) + (5.9×.10) + (7.3×.05) = 6.81`.

### Benchmark matrix

Current official public experiences were reviewed for ChatGPT, Claude, Gemini, Perplexity, Copilot, Mistral, Groq, OpenRouter, Cursor, Windsurf/Devin, Replit, Vercel, Linear, and Framer. The matrix extracts general product-design principles only; no brand assets, copy, layout, or signature interaction is copied.

| Area | VibeAI | Best benchmark | Gap | Improvement |
| --- | ---: | --- | --- | --- |
| Hero clarity | 6.0 | Cursor / Claude | Brand name and implementation provenance arrive before the job-to-be-done. | Lead with one concrete outcome and move technical proof below it. |
| Visual identity | 7.7 | Linear / Framer | Distinctive, but the huge glowing wordmark leans toward familiar AI aesthetics. | Keep the routing language while reducing spectacle and increasing editorial hierarchy. |
| Product proof | 6.4 | Cursor / Replit | Architecture diagrams are strong, but the real workspace is not introduced as product proof. | Name the actual authenticated workflow and clearly label illustrative traces. |
| Motion design | 7.8 | Framer / Linear | Purposeful hero motion, but timed sections run continuously. | Pause loops off-screen and preserve a complete static explanation. |
| Technical credibility | 7.2 | Mistral / OpenRouter | Deep detail exists, but stale metrics weaken confidence. | Derive or update claims from current code and explain boundaries. |
| Conversion | 6.3 | ChatGPT / Replit | CTA wording is product-specific, but the value before the CTA is abstract. | Use a single primary workspace action and a clear architecture secondary action. |
| Accessibility | 7.3 | ChatGPT / Cursor | Strong foundations; missing mobile section navigation and incomplete live-state semantics. | Add an accessible menu, status behavior, and keyboard verification. |
| Performance | 5.9 | Linear / Vercel | Landing eagerly includes authenticated application code. | Lazy-load the product routes, Clerk, and chat CSS. |
| Mobile experience | 6.5 | ChatGPT / Claude | Layout does not overflow, but navigation and hero density are compromised. | Add a compact menu, tighten hero copy, and verify touch targets. |
| Product interface | 7.1 | Claude / Linear | Capable authenticated product, but its relationship to the landing story is implicit. | Align product language, connection states, and proof with the public narrative. |

### Prioritized diagnosis

#### P1 — Core message is not immediately understandable

- Problem: the first visible headline is only the wordmark, followed by “Multi-Provider Multi-Agent Orchestration Core.”
- Evidence: the user outcome is buried in a 39-word paragraph and begins with “solo-built engineering project.”
- Likely cause: the page evolved as an engineering showcase before the authenticated product existed.
- User impact: a new visitor must decode architecture before knowing what the product does.
- Business impact: lower workspace conversion and weaker memorability.
- Technical risk: low.
- Correction: outcome-led headline, concise mechanism, truthful proof, and one primary CTA.

#### P1 — Public landing ships authenticated-product code

- Problem: Clerk, chat, projects, JSZip, and chat CSS are imported by the root route.
- Evidence: Vite reports one 675.01 kB JavaScript chunk.
- Likely cause: all hash routes are composed in `App.jsx` with eager imports.
- User impact: slower first interaction on the marketing page.
- Business impact: poorer first impression and performance score.
- Technical risk: medium because routing/auth behavior must remain identical.
- Correction: lazy product-route boundary with an accessible loading state; verify every hash route.

#### P1 — Product proof contains stale claims

- Problem: 38 entries, 10 providers, and 475 tests no longer match the repository.
- Evidence: `MODEL_REGISTRY` contains 39 entries and 11 provider identifiers; pytest collects 502 tests.
- Likely cause: hardcoded landing copy drifted from the backend-backed registry screen.
- User impact: technically sophisticated visitors lose trust.
- Business impact: credibility loss.
- Technical risk: low.
- Correction: update exact claims now and distinguish slots, provider routes, and collected tests.

#### P1 — Mobile section discovery disappears

- Problem: section links are hidden below 960 px without a replacement.
- Evidence: 390 px browser inspection shows every section link has zero rendered size.
- Likely cause: responsive CSS hides the desktop list but no compact menu was implemented.
- User impact: mobile visitors cannot scan or jump through the technical story.
- Business impact: reduced discovery of differentiators.
- Technical risk: low.
- Correction: accessible mobile menu with Escape, click-away, focus visibility, and 44 px targets.

#### P2 — Security and ecosystem context are incomplete

- Problem: the localhost warning appears late and connected products are absent.
- Evidence: the footer/engineering section mentions local binding, but not remote token requirements, guarded execution, or the broader product ecosystem.
- Likely cause: the landing page predates the current product surface.
- User impact: unclear deployment boundary and narrower perceived product value.
- Business impact: weaker confidence and differentiation.
- Technical risk: low if wording stays factual.
- Correction: add concise security/control and ecosystem sections sourced from current code/docs.

### Iteration 1 plan

- Files: `src/App.jsx`, new product-route boundary, `src/components/Hero.jsx`, `src/components/Nav.jsx`, `src/components/Engineering.jsx`, `src/data.js`, `src/App.css`, and this ledger.
- Objective: make the product clear within five seconds, correct stale proof, restore mobile navigation, and split the public/product bundles.
- Risks: hash-route regression, Clerk loading flash, menu focus/scroll behavior, and new responsive wrapping.
- Verification: lint, production build, route smoke tests, console, keyboard, reduced motion, 1440/1280/768/390 screenshots, and bundle comparison.

## Iteration 1 - Message, truth, navigation, and route boundary

### Changes

- Replaced the provenance-led hero with the outcome-led promise: "Complex work, orchestrated across models."
- Tightened the mechanism to plan, route, execute, critique, and verify; made the simulated trace label explicit.
- Corrected proof to 39 model slots, 11 provider routes, and 502 collected tests.
- Added a keyboard-operable mobile section menu with focus entry, Escape close, focus restoration, and 46 px targets.
- Moved Clerk, chat, projects, and chat CSS behind a lazy product-route boundary with an accessible loading state.
- Fixed the `CountUp` match dependency so the animation does not restart on every render.

### Evidence and result

- Landing JavaScript fell from 675.01 kB / 210.13 kB gzip to 381.81 kB / 121.74 kB gzip in the first split.
- Desktop and 390 px inspection showed no horizontal overflow.
- Mobile menu keyboard behavior and focus restoration passed manual browser checks.
- The primary CTA still resolves to the Clerk-gated workspace; authenticated views remain protected by `AuthProvider` and `Authed`.

## Iteration 2 - Product proof, control boundaries, and purposeful motion

### Changes

- Added an ecosystem section grounded in real repository surfaces: AI Workspace, Coding Agent, VibeMind, Video Observer, VS Code Extension, and Sensor Alerts.
- Added a security/control section that states the actual boundary: localhost-first, bearer token required remotely, credentials server-side, and guarded execution that is not a sandbox.
- Reframed inaccurate universal-cost and universal-quality claims as free-first routing with optional premium/local paths and explicit fallback limitations.
- Paused illustrative operation loops when off-screen or when reduced motion is requested.
- Updated engineering proof and provider naming from the current registry.

### Evidence and result

- Browser inspection at 1440 px showed the new ecosystem and control sections as legible structural proof rather than fake product screenshots.
- Reduced-motion CSS retains the full static explanation without requiring animation.
- The security wording matches the repository's localhost/token controls and does not claim isolation the code does not provide.

## Iteration 3 - Performance, SEO, and simplification

### Changes

- Split chat, project list, and project workspace into separate signed-in chunks after the shared authentication boundary.
- Removed Lenis and its unused hook; restored native scrolling for predictable anchors and keyboard behavior.
- Removed the external Google Fonts request and adopted a native-first font stack.
- Added complete title, description, Open Graph, Twitter, theme-color, and robots metadata.
- Centralized public metrics in `WEBSITE_METRICS` and replaced new cockpit-only CSS literals with semantic theme tokens.
- Removed the final lint warnings and upgraded audited dependencies with `npm audit fix`.

### Evidence and result

- Final landing JavaScript after review fixes: 368.55 kB raw / 118.19 kB gzip.
- Auth and signed-in surfaces are independently cached chunks (auth 25.63 kB gzip; chat 3.88 kB; project list 4.30 kB; workspace 5.26 kB).
- Final Lighthouse mobile production audit: Performance 97, Accessibility 100, Best Practices 100, SEO 100; FCP 1.5 s, LCP 1.8 s, TBT 190 ms, CLS 0.
- `npm audit --audit-level=moderate`: 0 vulnerabilities.

## Iteration 4 - Verification and adversarial review

The seven-lens review found two concrete frontend recovery/focus defects. Both were corrected before final scoring: rejected lazy chunks now render reload/back actions through a route error boundary, and mobile section activation moves focus to the destination after its menu unmounts. A maintainability concern about future metric drift remains documented below; the current values were verified against the repository in this run.

### Verification matrix

| Gate | Result | Notes |
| --- | --- | --- |
| JavaScript lint | PASS | `npm run lint`; zero warnings. |
| Production build | PASS | `npm run build`; no oversized-chunk warning. |
| Dependency audit | PASS | Zero known vulnerabilities at moderate-or-higher threshold. |
| Diff whitespace | PASS | `git diff --check`; only local line-ending notices. |
| Lighthouse performance | PASS | 97, above the 90 target. |
| Lighthouse accessibility | PASS | 100. |
| Lighthouse best practices | PASS | 100. |
| Lighthouse SEO | PASS | 100. |
| Responsive overflow | PASS | Browser checks at 1440 px and 390 px; CSS breakpoints cover 1280 px and 768 px. |
| Keyboard navigation | PASS | Mobile menu focus, Escape close, and focus restoration verified. |
| Python collection | PASS | 502 tests collected in 0.61 s. |
| Full Python suite | BLOCKED | 492 passed before two VibeMind automation failures from missing optional `pyautogui`, followed by interruption. This is outside the website package and predates this change. |
| Authenticated visual sweep | BLOCKED | Clerk redirected to its hosted development sign-in context; no user session was available for post-login screenshots. Code splitting, auth guards, and production compilation were verified. |
| JS unit/type checks | NOT CONFIGURED | This JavaScript package defines lint/build only; no unit-test script or TypeScript configuration exists. |

### Final scores

| Category | Before | After | Evidence |
| --- | ---: | ---: | --- |
| Messaging | 6.2 | 9.2 | Outcome-led promise, concise mechanism, explicit CTA hierarchy. |
| Visual design | 7.7 | 8.8 | Reduced spectacle, stronger editorial rhythm, cohesive proof sections. |
| Motion | 7.8 | 9.0 | Purposeful routing explanation, off-screen pause, reduced-motion parity. |
| UX | 6.4 | 8.8 | Mobile discovery, route loading, clearer workspace conversion path. |
| Trust | 6.3 | 9.2 | Current metrics, honest simulation label, concrete control boundaries. |
| Engineering | 7.0 | 8.9 | Route-level and surface-level splitting, recovery UI, clean lint/build/audit, centralized proof. |
| Performance | 5.9 | 9.5 | 45.4% smaller raw landing JavaScript and Lighthouse 97. |
| Accessibility | 7.3 | 10.0 | Lighthouse 100 plus verified menu keyboard behavior and motion preference. |

Final weighted score: **9.1 / 10**, up from **6.8 / 10**.

### Release assessment

The public landing page meets the quantitative quality bar and has no known P0/P1 landing defects. Full-product release confidence remains conditional on two external checks: a signed-in Clerk QA session and restoration of the optional desktop automation dependency needed by two root VibeMind tests. Neither limitation is hidden or represented as a passing gate.

## Iteration 5 - Cinematic Command Deck landing

### Changes

- Rebuilt the first screen as a responsive editorial Command Deck that explains manager-led multi-agent orchestration instead of imitating a single-model chat prompt.
- Added a visitor-controlled Planning, Routing, Critique, and Verification trace with pause, resume, replay, and direct stage inspection.
- Added an opt-in procedural sound layer that remains silent before consent and reports unsupported or rejected audio without blocking the experience.
- Preserved useful content under reduced or disabled motion, paused active work when the deck leaves the viewport, and stopped retained procedural animation loops outside full-motion mode.
- Added a focused Chromium Playwright contract covering composition, orchestration state, phone/tablet overflow, motion preferences, sound consent, keyboard behavior, anchors, accessibility, and build-revision identity.

### Release-candidate evidence

| Gate | Result | Notes |
| --- | --- | --- |
| JavaScript lint | PASS | `npm run lint`; zero warnings. |
| Production build | PASS | `npm run build`; landing JavaScript 375.87 kB raw / 120.36 kB gzip. The gzip increase is 1.84% from the 118.19 kB baseline, below the 10% review threshold. |
| Browser contract | PASS | 23/23 Chromium tests passed without retries. |
| Manual responsive QA | PASS | Desktop, 820 px tablet, and 390 px phone layouts inspected; no horizontal overflow or browser console warnings/errors. |
| Lighthouse mobile | PASS | Performance 99, Accessibility 100, Best Practices 100, SEO 100; FCP 1.5 s, LCP 1.7 s, TBT 0 ms, CLS 0. |
| Accessibility scan | PASS | Cockpit and Studio themes have no serious WCAG violations in the automated landing sweep. |

### Release boundary

- Linked Vercel project: `vibeai-showcase`; production alias: `https://vibeai-showcase.vercel.app/`.
- Previous known-good immutable deployment captured before release: `https://vibeai-showcase-jzgzx72yw-tamanpro26s-projects.vercel.app`.
- Deployment must come from the existing `codex/chat-orchestration-ui` branch, then pass the same browser contract against its immutable HTTPS deployment with the expected Git revision.
- Roll back by re-promoting the previous known-good deployment if the landing is blank, assets fail, the chat/auth handoff breaks, audio plays before consent, reduced-motion autoplay returns, or mobile overflow/runtime errors appear.
