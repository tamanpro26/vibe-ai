import { requireSession } from './_lib/clerkAuth.js'

/*
 * Proxy to the REAL VibeAI Manager/Team/Council backend -- the actual
 * multi-agent system (manager/claude_manager.py, teams/brain.py,
 * teams/code.py, core/confidence_cascade.py, core/comparison_judge.py),
 * not a single-model stand-in. Deployed separately (Render) since it's a
 * persistent Python process that can execute code and spawn subprocesses,
 * which a serverless function can't do.
 *
 * Both env vars below point at that deployment:
 *   VIBE_BACKEND_URL   e.g. https://vibeai-manager.onrender.com
 *   VIBE_API_TOKEN     must match the backend's own VIBE_API_TOKEN --
 *                      required because the backend binds 0.0.0.0
 *                      (api/server.py refuses mutating routes on a
 *                      non-localhost bind unless this is set)
 *
 * Gated behind a verified Clerk session for the same reason as
 * api/chat.js: an unauthenticated public endpoint sitting in front of a
 * token for a CODE-EXECUTION backend is a materially worse open-proxy
 * risk than the Groq key -- this is not merely "burn the free tier", it's
 * "run arbitrary code on our server."
 */

const VIBE_BACKEND_URL = process.env.VIBE_BACKEND_URL || ''
const VIBE_API_TOKEN = process.env.VIBE_API_TOKEN || ''

export default async function handler(req, res) {
  if (req.method === 'GET') {
    if (!VIBE_BACKEND_URL) {
      res.status(200).json({ ok: false, reason: 'VIBE_BACKEND_URL not configured' })
      return
    }
    try {
      const ctrl = new AbortController()
      const t = setTimeout(() => ctrl.abort(), 4000)
      const health = await fetch(`${VIBE_BACKEND_URL}/api/health`, { signal: ctrl.signal })
      clearTimeout(t)
      res.status(200).json({ ok: health.ok })
    } catch {
      res.status(200).json({ ok: false, reason: 'backend unreachable' })
    }
    return
  }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!VIBE_BACKEND_URL || !VIBE_API_TOKEN) {
    res.status(500).json({ error: 'server misconfigured: missing VIBE_BACKEND_URL or VIBE_API_TOKEN' })
    return
  }

  try {
    await requireSession(req)
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }

  const rawPrompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : ''

  /*
   * Personalization for the Manager tier.
   *
   * The Python backend takes a single prompt string, not an OpenAI-style
   * messages array, so there is no system role to use -- the profile has to
   * be prefixed onto the prompt itself. It is also STATELESS per call
   * (handle_user_request generates a fresh session id and keeps nothing), so
   * this must be re-sent on every request; there is no "set it once" option.
   *
   * Capped server-side for the same reason as chat.js: the browser limit is
   * a typing affordance, not a control.
   */
  const MAX_SYSTEM_CHARS = 4000
  const persona =
    typeof req.body?.systemPrompt === 'string'
      ? req.body.systemPrompt.trim().slice(0, MAX_SYSTEM_CHARS)
      : ''
  const prompt = persona ? `[About the user]\n${persona}\n\n[Request]\n${rawPrompt}` : rawPrompt

  if (!rawPrompt) {
    res.status(400).json({ error: 'prompt required' })
    return
  }

  try {
    const upstream = await fetch(`${VIBE_BACKEND_URL}/api/prompt`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${VIBE_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        prompt,
        session_id: typeof req.body?.session_id === 'string' ? req.body.session_id : undefined,
      }),
    })
    const data = await upstream.json()
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: data?.detail || 'upstream error' })
      return
    }
    res.status(200).json({ text: data.response, session_id: data.session_id })
  } catch (err) {
    res.status(502).json({ error: String(err?.message || err) })
  }
}
