import { requireSession } from './_lib/clerkAuth.js'

const VIBE_BACKEND_URL = process.env.VIBE_BACKEND_URL || ''
const VIBE_API_TOKEN = process.env.VIBE_API_TOKEN || ''

export const config = { maxDuration: 120 }

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!VIBE_BACKEND_URL || !VIBE_API_TOKEN) {
    res.status(500).json({ error: 'image backend is not configured' })
    return
  }
  try {
    await requireSession(req)
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }

  const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim().slice(0, 2000) : ''
  if (!prompt) {
    res.status(400).json({ error: 'prompt required' })
    return
  }

  try {
    const upstream = await fetch(`${VIBE_BACKEND_URL}/api/image/file`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${VIBE_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ prompt }),
    })
    if (!upstream.ok) {
      const data = await upstream.json().catch(() => null)
      res.status(upstream.status).json({ error: data?.detail || 'image generation failed' })
      return
    }
    const bytes = Buffer.from(await upstream.arrayBuffer())
    res.setHeader('Content-Type', upstream.headers.get('content-type') || 'image/jpeg')
    const source = upstream.headers.get('x-image-source')
    if (source) res.setHeader('X-Image-Source', source)
    res.status(200).send(bytes)
  } catch (error) {
    res.status(502).json({ error: String(error?.message || error) })
  }
}
