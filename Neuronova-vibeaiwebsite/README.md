# VibeAI — Project Website

Showcase site for **VibeAI**, a multi-provider multi-agent AI orchestration
system built entirely on free-tier LLMs. Single-page React app, dark
mission-control aesthetic, no backend — all "live" panels are labeled
simulations (the real API binds to localhost by design).

## Stack

- React 19 + Vite (React Compiler enabled)
- Plain CSS — design tokens in `src/index.css`, section styles in `src/App.css`
- Fonts: Chakra Petch (display), IBM Plex Mono (labels/logs), IBM Plex Sans (body)

## Develop

```bash
npm install
npm run dev
```

## Build

```bash
npm run build   # outputs static site to dist/
```

Deployable to any static host (GitHub Pages, Netlify, Cloudflare Pages).

## Content source

All claims (38 registry entries, 10 providers, 439 offline tests, council
stages, verifier battery) come from `../WEBSITE_BRIEF.md`. Update both when
numbers change.
