import { requireSessionContext } from './clerkAuth.js'

const VIBE_BACKEND_URL = process.env.VIBE_BACKEND_URL || ''
const VIBE_API_TOKEN = process.env.VIBE_API_TOKEN || ''

export const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/

export async function forwardVibe(req, res, route) {
  if (!VIBE_BACKEND_URL || !VIBE_API_TOKEN) {
    res.status(500).json({ error: 'capability backend is not configured' })
    return
  }
  let context
  try {
    context = await requireSessionContext(req)
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }
  try {
    const upstream = await fetch(`${VIBE_BACKEND_URL}${route.path}`, {
      method: route.method,
      headers: {
        Authorization: `Bearer ${VIBE_API_TOKEN}`,
        'Content-Type': 'application/json',
        'X-Vibe-User-Token': context.token,
      },
      body: route.body === undefined ? undefined : JSON.stringify(route.body),
    })
    const data = await upstream.json().catch(() => ({}))
    res.status(upstream.status).json(upstream.ok ? data : { error: data?.detail || 'upstream error' })
  } catch (error) {
    res.status(502).json({ error: String(error?.message || error) })
  }
}
