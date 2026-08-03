/*
 * Read-only proxy to the backend's GET /api/registry — the real model roster.
 *
 * No Clerk gate, unlike team.js/chat.js/project.js: this endpoint holds no key
 * and triggers no model call or code execution. It returns the same capability
 * inventory the public landing page already advertises, so gating it would only
 * mean the site's own numbers could not be read from the system they describe.
 *
 * A proxy is needed at all because the backend's CORS allowlist is localhost +
 * the VS Code webview only (api/server.py) — a browser on the deployed origin
 * cannot call it directly, by design.
 *
 * Cached at the edge: the roster only changes when the backend is redeployed,
 * so re-fetching it per visitor is pure waste.
 */

const VIBE_BACKEND_URL = process.env.VIBE_BACKEND_URL || ''

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!VIBE_BACKEND_URL) {
    res.status(200).json({ ok: false, reason: 'VIBE_BACKEND_URL not configured', entries: [] })
    return
  }

  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 6000)
    const upstream = await fetch(`${VIBE_BACKEND_URL}/api/registry`, { signal: ctrl.signal })
    clearTimeout(t)
    if (!upstream.ok) {
      res.status(200).json({ ok: false, reason: `upstream ${upstream.status}`, entries: [] })
      return
    }
    const data = await upstream.json()
    res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=3600')
    res.status(200).json({ ok: true, ...data })
  } catch {
    // Degrade to "unknown", never to invented numbers: the Registry surface
    // shows an unavailable state rather than a plausible-looking roster.
    res.status(200).json({ ok: false, reason: 'backend unreachable', entries: [] })
  }
}
