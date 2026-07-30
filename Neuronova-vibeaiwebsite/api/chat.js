import { createRemoteJWKSet, jwtVerify } from 'jose'

/*
 * Server-side chat proxy for the deployed (public) site.
 *
 * OmniRoute and the VibeAI Manager API only run on localhost, so a browser
 * on the public internet can never reach them -- that's what "localhost"
 * means, not a bug. This function is the actual fix: it holds a real
 * provider key (Groq, GROQ_API_KEY below) as a server-only env var, never
 * shipped to the browser, and answers on our own domain instead.
 *
 * Gated behind a verified Clerk session token rather than left open: an
 * unauthenticated public endpoint sitting in front of our own provider key
 * is an open proxy anyone on the internet could hammer to burn the free
 * tier -- the same class of risk already flagged for Firecrawl earlier in
 * this project (a browser-callable key-holding endpoint gets scraped).
 * Verification uses Clerk's public JWKS (no secret key needed -- only the
 * publishable key's own instance domain), so no new credential was required
 * to add this.
 */

const PUBLISHABLE_KEY = process.env.VITE_CLERK_PUBLISHABLE_KEY || ''
const GROQ_API_KEY = process.env.GROQ_API_KEY

/*
 * Mode -> {model, maxTokens} is a server-side WHITELIST, not a passthrough of
 * client-supplied values: an open proxy that lets the caller pick its own
 * model/max_tokens is an easy way to run up cost on someone else's key. The
 * client only ever sends the mode NAME; this table is the only thing that
 * decides what actually gets billed.
 *
 * gpt-oss-120b (deep) needs the generous ceiling -- verified live: with only
 * 2048 tokens it can spend the entire budget on hidden reasoning tokens and
 * return empty content (finish_reason "length", 0 visible chars). The same
 * failure mode this project hit and fixed for OmniRoute's own auto/* router
 * earlier.
 */
const MODES = {
  fast: { model: 'llama-3.1-8b-instant', maxTokens: 1024 },
  balanced: { model: 'llama-3.3-70b-versatile', maxTokens: 3072 },
  deep: { model: 'openai/gpt-oss-120b', maxTokens: 8192 },
}

function clerkIssuer(publishableKey) {
  const encoded = publishableKey.replace(/^pk_(test|live)_/, '').replace(/\$$/, '')
  const domain = Buffer.from(encoded, 'base64').toString('utf8')
  return `https://${domain}`
}

let jwks = null
function getJwks() {
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${clerkIssuer(PUBLISHABLE_KEY)}/.well-known/jwks.json`))
  }
  return jwks
}

async function requireSession(req) {
  const auth = req.headers.authorization || ''
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : null
  if (!token) throw new Error('missing session token')
  await jwtVerify(token, getJwks(), { issuer: clerkIssuer(PUBLISHABLE_KEY) })
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    res.status(200).json({ ok: true })
    return
  }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!GROQ_API_KEY) {
    res.status(500).json({ error: 'server misconfigured: missing GROQ_API_KEY' })
    return
  }

  try {
    await requireSession(req)
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }

  const messages = Array.isArray(req.body?.messages) ? req.body.messages : null
  if (!messages || messages.length === 0) {
    res.status(400).json({ error: 'messages required' })
    return
  }
  const cfg = MODES[req.body?.mode] || MODES.balanced

  try {
    const upstream = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${GROQ_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: cfg.model,
        messages,
        max_tokens: cfg.maxTokens,
      }),
    })
    const data = await upstream.json()
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: data?.error?.message || 'upstream error' })
      return
    }
    const choice = data?.choices?.[0]
    const text = choice?.message?.content?.trim() || ''
    if (!text) {
      res.status(502).json({ error: 'empty upstream response' })
      return
    }
    // finish_reason "length" means the model hit max_tokens mid-answer, not
    // that it finished naturally. Surfacing it lets the UI show a real
    // "cut short" state instead of silently presenting a truncated answer
    // as if it were complete -- verified reproducing exactly that failure
    // with the previous fixed 2048 ceiling on a moderately detailed prompt.
    res.status(200).json({ text, truncated: choice?.finish_reason === 'length' })
  } catch (err) {
    res.status(502).json({ error: String(err?.message || err) })
  }
}
