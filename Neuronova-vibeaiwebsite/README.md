# VibeAI — Project Website

Public site and authenticated workspace for **VibeAI**, a multi-provider,
multi-agent AI orchestration system. The React application includes the
landing experience, Clerk-authenticated chat and project routes, and
serverless adapters for the local/deployed VibeAI backend. Illustrative traces
are labeled as such; provider responses come from configured live engine tiers.

## Stack

- React 19 + Vite (React Compiler enabled)
- Plain CSS — design tokens in `src/index.css`, section styles in `src/App.css`
- Authentication: Clerk
- Motion: Motion, with native browser scrolling
- Fonts: Inter plus the platform monospace stack

## Develop

```bash
npm install
npm run dev
```

## Build

```bash
npm run build   # outputs static site to dist/
```

The landing route builds as static assets. Authenticated serverless API routes
require a compatible deployment environment and their documented environment
configuration.

## Content source

Current product claims follow `PRODUCT.md` and the repository source. As of
2026-08-06, the code contains 39 model registry slots, 11 provider identifiers,
and 502 pytest-collected tests. Re-run the registry/test checks before changing
these figures; do not rely on older website briefs.
