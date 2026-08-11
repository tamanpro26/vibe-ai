import { createRemoteJWKSet, jwtVerify } from 'jose'

/*
 * Shared Clerk session verification for server-side proxies (api/chat.js,
 * api/team.js). Both sit in front of a real provider key / real backend
 * token, so both need the same "prove this came from a logged-in browser"
 * gate -- an unauthenticated endpoint holding either secret is an open
 * proxy anyone on the internet could hammer.
 *
 * Uses Clerk's public JWKS (no secret key needed, only the publishable
 * key's own instance domain) -- kept in one place so the two proxies can't
 * drift into different verification logic over time.
 */

const PUBLISHABLE_KEY = process.env.VITE_CLERK_PUBLISHABLE_KEY || ''

function clerkIssuer(publishableKey) {
  // Clerk publishable keys are `pk_<env>_` + base64(`<frontend-api-domain>$`)
  // -- the trailing "$" is part of the DECODED domain string, not the
  // base64 form (base64 never contains "$"). Stripping it beforehand was a
  // no-op; every issuer built here carried a literal trailing "$" onto the
  // real domain, which broke both the JWKS fetch URL and the `issuer`
  // check in jwtVerify below -- confirmed live via Vercel's own request
  // logs: every real, valid session token was rejected with 401, because
  // the token's genuine `iss` claim (the correct domain, no "$") could
  // never match this malformed one.
  const encoded = publishableKey.replace(/^pk_(test|live)_/, '')
  const domain = Buffer.from(encoded, 'base64').toString('utf8').replace(/\$$/, '')
  return `https://${domain}`
}

let jwks = null
function getJwks() {
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${clerkIssuer(PUBLISHABLE_KEY)}/.well-known/jwks.json`))
  }
  return jwks
}

export async function requireSession(req) {
  const token = sessionToken(req)
  if (!token) throw new Error('missing session token')
  const { payload } = await jwtVerify(token, getJwks(), { issuer: clerkIssuer(PUBLISHABLE_KEY) })
  return payload
}

export function sessionToken(req) {
  const auth = req.headers.authorization || ''
  return auth.startsWith('Bearer ') ? auth.slice(7) : null
}

export async function requireSessionContext(req) {
  const token = sessionToken(req)
  if (!token) throw new Error('missing session token')
  const payload = await requireSession(req)
  return { payload, token }
}
