import { requireSession } from './_lib/clerkAuth.js'

/*
 * Server-side web-search proxy (Exa), for real research in the deployed chat.
 *
 * Why this file exists: the browser-side research path is Wikipedia-only,
 * because it runs in the bundle and a search key cannot be shipped there --
 * anyone could read it out of the JS. Wikipedia needs no key, but it also
 * cannot answer anything current, niche, or not encyclopaedic, so those
 * questions degraded to an ungrounded answer.
 *
 * Same shape as chat.js: the key lives ONLY in a server env var, and the
 * endpoint is gated behind a verified Clerk session. An open, unauthenticated
 * endpoint sitting in front of a paid search key is an open proxy that will
 * get scraped and billed to us.
 *
 * Requires EXA_API_KEY in the Vercel project env (the key already exists in
 * the Python side's .env; it has to be added to the website project too).
 */

const EXA_API_KEY = process.env.EXA_API_KEY

// Server-side caps, not client-controlled: the caller sends a query only.
// Letting the browser choose result counts or content length is how a proxy
// like this turns into an expensive way to fetch the whole web.
const MAX_RESULTS = 6
const MAX_CHARS_PER_RESULT = 1200
const MAX_QUERY_CHARS = 300

export default async function handler(req, res) {
  // GET is the capability probe the client uses to decide whether real search
  // is available at all, so it must answer without doing a paid lookup.
  if (req.method === 'GET') {
    res.status(200).json({ ok: true, available: Boolean(EXA_API_KEY) })
    return
  }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!EXA_API_KEY) {
    // 501, not 500: this is "feature not configured", which the client treats
    // as a clean signal to fall back to Wikipedia rather than as an outage.
    res.status(501).json({ error: 'search not configured' })
    return
  }

  try {
    await requireSession(req)
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }

  const query = typeof req.body?.query === 'string' ? req.body.query.trim().slice(0, MAX_QUERY_CHARS) : ''
  if (!query) {
    res.status(400).json({ error: 'query required' })
    return
  }

  try {
    const upstream = await fetch('https://api.exa.ai/search', {
      method: 'POST',
      headers: { 'x-api-key': EXA_API_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        numResults: MAX_RESULTS,
        type: 'auto',
        contents: { text: { maxCharacters: MAX_CHARS_PER_RESULT } },
      }),
    })

    const data = await upstream.json()
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: data?.error || 'search upstream error' })
      return
    }

    // Normalised to the same shape the existing Wikipedia path already
    // returns, so the client renders both identically and the fallback is
    // invisible to the UI layer.
    const sources = (data?.results || [])
      .filter((r) => r?.url)
      .map((r) => ({
        title: r.title || r.url,
        url: r.url,
        site: safeHost(r.url),
        extract: (r.text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_CHARS_PER_RESULT),
        image: r.image || null,
        published: r.publishedDate || null,
      }))

    res.status(200).json({ sources })
  } catch (err) {
    res.status(502).json({ error: String(err?.message || err) })
  }
}

function safeHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}
