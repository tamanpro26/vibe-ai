import JSZip from 'jszip'

/*
 * Two engines:
 *  - LIVE: the real VibeAI FastAPI server on localhost:8000 — every prompt
 *    goes through manager.handle_user_request (the actual Manager AI).
 *  - SIMULATED: template fallback used when the local server isn't running,
 *    e.g. on a public static host. The real API executes code and binds to
 *    localhost only, so the hosted site can never reach it by design.
 */

const API_BASE = 'http://127.0.0.1:8000'

function authHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  // Optional: localStorage.setItem('vibeai_api_token', '<token>') when the
  // server runs with VIBE_API_TOKEN set. Localhost dev needs no token.
  const token = localStorage.getItem('vibeai_api_token')
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export async function checkLive() {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 1500)
    const res = await fetch(`${API_BASE}/api/health`, { signal: ctrl.signal })
    clearTimeout(t)
    return res.ok
  } catch {
    return false
  }
}

/* ── Research with images (keyless) ────────────────────────────────────────
 * ChatGPT-style research: look the topic up, show real images from the web,
 * and write an answer grounded in what was actually retrieved.
 *
 * Wikipedia is used rather than a commercial search API for one concrete
 * reason: it needs no API key. Firecrawl returns better images and DOES allow
 * browser calls, but only by shipping the key in this bundle where anyone can
 * read it. Wikipedia's REST + action APIs both send
 * `access-control-allow-origin: *` (verified 2026-07-28), so this works from
 * the browser, on a public host, with no secret at all.
 */
const WIKI_SEARCH = 'https://en.wikipedia.org/w/api.php'
const WIKI_SUMMARY = 'https://en.wikipedia.org/api/rest_v1/page/summary/'

const RESEARCH_LEAD = /\b(who|what|tell me|info|information|about|research|explain|overview|history|compare|list)\b/i
const RESEARCH_HINT = /\b(compan(y|ies)|brand|organi[sz]ation|business|industry|corporation|founder|ceo|product|person|city|country|university|history of)\b/i

/*
 * Anything time-sensitive. These are the questions Wikipedia structurally
 * cannot answer, so before real search existed they were pointless to route
 * here -- now they are the single strongest reason TO route here.
 */
const RESEARCH_RECENCY =
  /\b(latest|current|recent|today|yesterday|now|this (week|month|year)|202[4-9]|news|update[ds]?|price|stock|release[ds]?|announce[ds]?|who won|score|weather|status)\b/i

/* Explicit asks to look something up -- unambiguous, no second signal needed. */
const RESEARCH_EXPLICIT =
  /\b(search|look ?up|google|find out|sources?|cite|citation|according to|on the (web|internet))\b/i

/* Requests to WRITE something, which must not be hijacked into a research
   lookup even though they often contain words like "explain" or "list". */
const MAKE_REQUEST =
  /\b(write|code|implement|refactor|debug|fix|build|create|generate|convert|translate|summari[sz]e this|rewrite)\b/i

export function isResearchRequest(text) {
  const t = text || ''
  if (isImageRequest(t)) return false          // "draw a mango" is not research
  if (t.trim().length < 8) return false        // greetings are not research
  if (MAKE_REQUEST.test(t)) return false       // "write a function" is a task, not a lookup

  // Any one of these is sufficient. The original rule required an entity
  // keyword (company/brand/city/...) AND a question lead, which meant the
  // common cases -- "what's the latest on X", "search for Y" -- never
  // triggered research at all and silently answered from model memory.
  if (RESEARCH_EXPLICIT.test(t)) return true
  if (RESEARCH_RECENCY.test(t)) return true
  return RESEARCH_HINT.test(t) && RESEARCH_LEAD.test(t)
}

/** Strip the ask so Wikipedia receives the subject, not the instruction. */
export function researchQuery(text) {
  return (text || '')
    .replace(/\b(please|can you|could you|give me|provide me|tell me|i want|i need|some|info(rmation)?|about|regarding|research|overview|of|the)\b/gi, ' ')
    .replace(/[?!.]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim() || text
}

const COMMONS_API = 'https://commons.wikimedia.org/w/api.php'

/* Wikipedia's own pageimages API returns nothing for most COMPANY articles,
 * because brand logos are non-free and Wikipedia will not serve them through
 * the API. Verified: "Mango (retailer)" has no thumbnail or originalimage at
 * all, while "Apple Inc." does (its logo lives on Commons). Since companies
 * are exactly what this feature is for, fall back to a Commons media search,
 * which surfaces freely-licensed photographs (storefronts, buildings, products)
 * for the same entity. Also keyless and CORS-open. */
async function commonsImage(title) {
  const params = new URLSearchParams({
    action: 'query', generator: 'search', gsrsearch: title,
    gsrnamespace: '6',          // File: namespace
    gsrlimit: '1', prop: 'imageinfo', iiprop: 'url',
    iiurlwidth: '400', format: 'json', origin: '*',
  })
  try {
    const r = await fetch(`${COMMONS_API}?${params}`)
    if (!r.ok) return null
    const pages = (await r.json())?.query?.pages || {}
    const first = Object.values(pages)[0]
    return first?.imageinfo?.[0]?.thumburl || null
  } catch {
    return null
  }
}

/*
 * Real web search via the server-side Exa proxy (api/research.js).
 *
 * Returns [] rather than throwing on ANY failure -- unconfigured (501), not
 * signed in (401), network error -- because the caller treats an empty result
 * as "fall back to Wikipedia". A throw here would take down research entirely
 * on a deployment where the search key simply is not set, which is a worse
 * outcome than the encyclopaedic-only answer we had before.
 */
export async function webSearchSources(query) {
  try {
    const res = await fetch('/api/research', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
    if (!res.ok) return []
    const data = await res.json()
    return (data?.sources || [])
      .filter((s) => s.extract)
      .map((s) => ({
        title: s.title,
        extract: s.extract,
        thumb: s.image || null,
        url: s.url,
        site: s.site,
        published: s.published || null,
      }))
  } catch {
    return []
  }
}

/** Search Wikipedia, then hydrate each hit with its extract + thumbnail. */
export async function researchSources(query, limit = 4) {
  const params = new URLSearchParams({
    action: 'query', list: 'search', srsearch: query,
    format: 'json', origin: '*', srlimit: String(limit),
  })
  const res = await fetch(`${WIKI_SEARCH}?${params}`)
  if (!res.ok) throw new Error(`wikipedia search ${res.status}`)
  const hits = (await res.json())?.query?.search || []

  // Hydrate in parallel; a single dead page must not sink the whole result set.
  const settled = await Promise.allSettled(
    hits.map(async (h) => {
      const r = await fetch(WIKI_SUMMARY + encodeURIComponent(h.title.replace(/ /g, '_')))
      if (!r.ok) throw new Error(String(r.status))
      const s = await r.json()
      const thumb = s.thumbnail?.source || (await commonsImage(s.title || h.title))
      return {
        title: s.title,
        extract: s.extract || '',
        thumb,
        url: s.content_urls?.desktop?.page || `https://en.wikipedia.org/wiki/${encodeURIComponent(h.title)}`,
        site: 'wikipedia.org',
      }
    }),
  )
  return settled.filter((x) => x.status === 'fulfilled' && x.value.extract).map((x) => x.value)
}

export async function respondResearch(prompt, history = [], mode = DEFAULT_MODE) {
  // Real web search first, Wikipedia only as the fallback. Order matters:
  // Exa can answer current/niche/non-encyclopaedic questions that Wikipedia
  // structurally cannot, and those are exactly the ones that used to fall
  // through to an ungrounded answer.
  //
  // Note the query sent to each differs on purpose. Wikipedia is a title
  // index, so it needs the bare subject (researchQuery strips the ask). A
  // real search engine does better with the FULL natural question, since the
  // extra words carry intent -- stripping them there would throw away signal.
  let sources = await webSearchSources(prompt)
  if (!sources.length) sources = await researchSources(researchQuery(prompt))
  if (!sources.length) throw new Error('no sources found')

  const context = sources
    .map((s, i) => `[${i + 1}] ${s.title}\n${s.extract}`)
    .join('\n\n')

  const cfg = modeConfig(mode)
  // The grounding rule is not decoration. Correct retrieved context alone does
  // NOT stop a model inventing specifics -- during this project's CLI work a
  // model produced a confident wrong figure plus a fabricated citation marker
  // from otherwise-correct snippets. State the constraint explicitly.
  const { text, truncated } = await omniComplete(
    [
      {
        role: 'system',
        content:
          OMNI_SYSTEM +
          (cfg.hint ? `\n\n${cfg.hint}` : '') +
          '\n\nSOURCES are supplied below. Ground your answer in them. State only ' +
          'facts that appear in the sources; if the sources do not cover something ' +
          'the user asked, say so plainly rather than filling the gap from memory. ' +
          'Refer to sources by name (e.g. "Mango (retailer)"), never invent a ' +
          'footnote marker or citation number. Do not list the sources at the end; ' +
          'they are rendered separately by the interface.',
      },
      ...history.slice(-6).map((m) => ({ role: m.role, content: m.content })),
      { role: 'user', content: `SOURCES:\n${context}\n\n---\nQuestion: ${prompt}` },
    ],
    { model: cfg.omniModel, maxTokens: cfg.maxTokens },
  )
  return { text, project: null, image: null, sources, truncated }
}

/* ── OmniRoute: real answers, in the browser ───────────────────────────────
 * The simulated engine can only return canned templates, so "hi, whats up"
 * got a paragraph about model registries. OmniRoute is a local OpenAI-
 * compatible gateway aggregating free-tier providers, and it answers real
 * questions.
 *
 * Verified live 2026-07-28 from this exact origin:
 *   - CORS: access-control-allow-origin: http://localhost:5173 (preflight 204)
 *   - Auth: NOT required on loopback (HTTP 200 with no Authorization header)
 * So no API key is embedded in this bundle and none is needed.
 */
const OMNI_BASE = 'http://localhost:20128/v1'

/*
 * Reasoning modes. Each maps to a verified-working OmniRoute `auto/*` alias
 * (local engine) -- api/chat.js has the matching server-side table for the
 * Groq edge proxy used on the public deploy.
 *
 * maxTokens is not a nicety, it's the fix for a reproduced bug: the road-
 * crossing prompt got silently cut off mid-sentence with the old flat 2048
 * ceiling (verified live: finish_reason "length", cut off at "Stand a
 * little **back from"). "deep" needs the largest ceiling of all -- verified
 * that a reasoning model can burn its ENTIRE budget on hidden reasoning
 * tokens and return zero visible characters if the ceiling is too tight.
 */
export const DEFAULT_MODE = 'balanced'
const MODES = {
  fast: {
    omniModel: 'auto/best-fast',
    maxTokens: 1024,
    hint: 'Prioritize speed: answer directly in as few words as the question reasonably allows. Skip preamble and caveats unless safety-relevant.',
  },
  balanced: {
    omniModel: 'auto/best-chat',
    maxTokens: 3072,
    hint: '',
  },
  deep: {
    omniModel: 'auto/best-reasoning',
    maxTokens: 8192,
    hint: 'Think it through carefully before answering. For non-trivial questions, reason step by step and consider edge cases or alternatives before settling on a final answer.',
  },
}
const modeConfig = (mode) => MODES[mode] || MODES[DEFAULT_MODE]

const OMNI_SYSTEM =
  'You are VibeAI, a multi-provider AI assistant. Answer the user directly, ' +
  'accurately and conversationally. Match their tone: a greeting gets a short ' +
  'friendly reply, a real question gets a real answer. Use markdown when it ' +
  'helps (code blocks for code). Never describe your own routing, teams or ' +
  'architecture unless the user asks about them.\n' +
  'Be warm and expressive: use emojis naturally to give the chat personality. ' +
  'Lead section headings with a fitting emoji and use them to mark points in ' +
  'lists. Aim for roughly one every few lines, where they add warmth or help ' +
  'the reader scan. Keep them OUT of code blocks, and do not let them replace ' +
  'the actual answer.'

export async function checkOmni() {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 2500)
    const res = await fetch(`${OMNI_BASE}/models`, { signal: ctrl.signal })
    clearTimeout(t)
    return res.ok
  } catch {
    return false
  }
}

/**
 * Shared OmniRoute call. Returns { text, truncated }.
 *
 * `truncated` is true when the upstream's own finish_reason says the reply
 * was cut off by max_tokens rather than finishing naturally -- previously
 * this returned a bare string and dropped that signal entirely, so a
 * cut-off reply looked exactly like a complete one to every caller.
 */
export async function omniComplete(messages, { model = MODES[DEFAULT_MODE].omniModel, maxTokens = MODES[DEFAULT_MODE].maxTokens } = {}) {
  // Streamed, not because we render tokens live, but because OmniRoute's
  // "auto/*" routes never return a non-streaming body (verified: stream:false
  // hangs indefinitely, streaming returns at once).
  const res = await fetch(`${OMNI_BASE}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      stream: true,
      max_tokens: maxTokens,
      messages,
    }),
  })
  if (!res.ok) throw new Error(`OmniRoute ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let out = ''
  let finishReason = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''            // keep the incomplete tail
    for (const line of lines) {
      const s = line.trim()
      // ':' prefixed lines are SSE comments; OmniRoute uses them for its
      // x-omniroute-* telemetry trailer.
      if (!s.startsWith('data:')) continue
      const payload = s.slice(5).trim()
      if (payload === '[DONE]') continue
      try {
        const j = JSON.parse(payload)
        const d = j.choices?.[0]
        if (d?.delta?.content) out += d.delta.content
        if (d?.finish_reason) finishReason = d.finish_reason
      } catch {
        /* keepalive or partial frame, skip */
      }
    }
  }
  if (!out.trim()) throw new Error('OmniRoute returned empty content')
  return { text: out.trim(), truncated: finishReason === 'length' }
}

export async function respondOmni(prompt, history = [], mode = DEFAULT_MODE, systemPrompt = '') {
  if (isImageRequest(prompt)) return imageReply(prompt, '\n')

  // Research is attempted first, but never at the cost of an answer: if
  // Wikipedia is unreachable or has nothing on the topic, fall through to a
  // plain model reply rather than failing the turn.
  if (isResearchRequest(prompt)) {
    try {
      return await respondResearch(prompt, history, mode)
    } catch {
      /* fall through to the plain reply below */
    }
  }

  const cfg = modeConfig(mode)
  let sys = cfg.hint ? `${OMNI_SYSTEM}\n\n${cfg.hint}` : OMNI_SYSTEM
  // OmniRoute is a direct local call with no proxy in between, so the profile
  // is folded into the system message here rather than being sent as its own
  // field the way the /api/* tiers do it.
  if (systemPrompt) sys = `${sys}\n\nAbout the user:\n${systemPrompt}`
  const { text, truncated } = await omniComplete(
    [
      { role: 'system', content: sys },
      ...history.slice(-8).map((m) => ({ role: m.role, content: m.content })),
      { role: 'user', content: prompt },
    ],
    { model: cfg.omniModel, maxTokens: cfg.maxTokens },
  )
  return { text, project: null, image: null, truncated }
}

/* ── Edge proxy: real answers on the public URL ────────────────────────────
 * OmniRoute and the VibeAI Manager API are localhost-only, so the hosted
 * site could never reach them -- not a bug, just what "localhost" means from
 * a browser on someone else's machine. api/chat.js is the actual fix: a
 * Vercel serverless function holding a real provider key (Groq) server-side,
 * reachable at our own same-origin /api/chat instead.
 *
 * Requires a Clerk session token: an unauthenticated public endpoint sitting
 * in front of a real provider key is an open proxy anyone on the internet
 * could hammer to burn the free tier, so ChatApp passes the caller's current
 * session token rather than this being reachable anonymously.
 */
export async function checkEdge() {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 2500)
    const res = await fetch('/api/chat', { signal: ctrl.signal })
    clearTimeout(t)
    return res.ok
  } catch {
    return false
  }
}

export async function respondEdge(prompt, history = [], token, mode = DEFAULT_MODE, systemPrompt = '') {
  if (isImageRequest(prompt)) return imageReply(prompt, '\n')
  if (isResearchRequest(prompt)) {
    try {
      // NOTE: respondResearch's grounding step calls omniComplete, which is
      // OmniRoute-only (localhost). On the public deploy that fetch always
      // fails, so this always falls through to the plain (ungrounded) reply
      // below -- a real, separate gap in the research feature, not
      // introduced by mode support. Left as-is here; fixing it means giving
      // respondResearch an injected completion function per engine tier.
      return await respondResearch(prompt, history, mode)
    } catch {
      /* fall through to the plain reply below */
    }
  }

  const cfg = modeConfig(mode)
  const sys = cfg.hint ? `${OMNI_SYSTEM}\n\n${cfg.hint}` : OMNI_SYSTEM
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      mode,
      systemPrompt,
      messages: [
        { role: 'system', content: sys },
        ...history.slice(-8).map((m) => ({ role: m.role, content: m.content })),
        { role: 'user', content: prompt },
      ],
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.error || `edge proxy ${res.status}`)
  return { text: data.text, project: null, image: null, truncated: !!data.truncated }
}

/* ── Continuing a truncated reply ───────────────────────────────────────────
 * Feeds the partial answer back as the assistant's own last turn and asks
 * for a seamless continuation, rather than silently presenting a cut-off
 * answer as complete (the road-crossing bug this session reproduced live:
 * finish_reason "length", 2048-token ceiling, cut off mid-sentence with no
 * error and no way to recover the rest).
 */
const CONTINUE_INSTRUCTION =
  'Continue exactly where you left off. Do not repeat any earlier text and ' +
  'do not re-introduce the topic -- just keep writing from the exact cutoff point.'

export async function continueOmni(history, partialText, mode = DEFAULT_MODE) {
  const cfg = modeConfig(mode)
  const sys = cfg.hint ? `${OMNI_SYSTEM}\n\n${cfg.hint}` : OMNI_SYSTEM
  return omniComplete(
    [
      { role: 'system', content: sys },
      ...history.slice(-8).map((m) => ({ role: m.role, content: m.content })),
      { role: 'assistant', content: partialText },
      { role: 'user', content: CONTINUE_INSTRUCTION },
    ],
    { model: cfg.omniModel, maxTokens: cfg.maxTokens },
  )
}

export async function continueEdge(history, partialText, token, mode = DEFAULT_MODE) {
  const cfg = modeConfig(mode)
  const sys = cfg.hint ? `${OMNI_SYSTEM}\n\n${cfg.hint}` : OMNI_SYSTEM
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      mode,
      messages: [
        { role: 'system', content: sys },
        ...history.slice(-8).map((m) => ({ role: m.role, content: m.content })),
        { role: 'assistant', content: partialText },
        { role: 'user', content: CONTINUE_INSTRUCTION },
      ],
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.error || `edge proxy ${res.status}`)
  return { text: data.text, truncated: !!data.truncated }
}

export async function respondLive(prompt, sessionId, team = 'auto', mode = DEFAULT_MODE, imageB64 = '') {
  // Image requests are served browser-side even in LIVE mode: /api/prompt
  // returns prose, not pictures, so routing an image ask through it would
  // yield a description of an image rather than an image.
  if (isImageRequest(prompt)) return imageReply(prompt, '\n')

  const res = await fetch(`${API_BASE}/api/prompt`, {
    method: 'POST',
    headers: authHeaders(),
    // Same /api/prompt the manager proxy calls, so it carries an attached
    // image too -- otherwise local dev would answer blind against a backend
    // that supports vision perfectly well.
    body: JSON.stringify({
      prompt,
      session_id: sessionId,
      team,
      reasoning_mode: mode,
      image_b64: imageB64 || undefined,
    }),
  })
  if (!res.ok) throw new Error(`API returned ${res.status}`)
  const data = await res.json()
  return { text: data.response, project: null }
}

/* ── Team proxy: the real Manager, reached from the public URL ─────────────
 * respondLive above only ever works from localhost -- the real Manager API
 * binds there by design (it can execute code). This is the SAME real
 * multi-agent system (manager/claude_manager.py, teams/*, confidence
 * cascade, comparison judge), reached instead through our own Vercel
 * function (api/team.js), which holds the backend's bearer token
 * server-side and forwards to wherever that backend is actually hosted
 * (Render). Without this, the public site's chat degrades to a single
 * model (OmniRoute/Groq) even though the real multi-agent system exists --
 * that gap is exactly what this closes.
 */
export async function checkTeam() {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 4000)
    const res = await fetch('/api/team', { signal: ctrl.signal })
    clearTimeout(t)
    if (!res.ok) return false
    const data = await res.json()
    return !!data.ok
  } catch {
    return false
  }
}

export async function respondTeam(
  prompt, sessionId, token, systemPrompt = '', team = 'auto', mode = DEFAULT_MODE, imageB64 = '',
) {
  if (isImageRequest(prompt)) return imageReply(prompt, '\n')

  const res = await fetch('/api/team', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      prompt,
      session_id: sessionId,
      systemPrompt,
      team,
      reasoning_mode: mode,
      // The only route to the vision team from a browser. See api/team.js.
      image_b64: imageB64 || undefined,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.error || `team proxy ${res.status}`)
  return { text: data.text, project: null }
}

/* ── Attachments: read the actual bytes ────────────────────────────────────
 * ChatApp used to keep only {name, size}, so nothing about an attached file
 * ever reached a model. These read real content in the browser:
 *   image/*  -> base64, sent as image_b64 for the vision team
 *   text     -> inlined into the prompt as context
 *   video    -> flagged unsupported (see below), never silently ignored
 *
 * Video is deliberately NOT base64'd into the prompt. The vision team wants
 * a server-side video_path or a pre-extracted .frames directory (it samples
 * frames), and real videos blow past Vercel's ~4.5MB body limit anyway.
 * Routing video properly means POSTing the file to the backend's /api/video,
 * which is a separate upload path -- until that exists, saying so beats
 * pretending.
 */
const MAX_IMAGE_BYTES = 2.5 * 1024 * 1024 // ~3.4MB base64, under the proxy cap
const MAX_TEXT_BYTES = 128 * 1024
const TEXT_RE = /\.(txt|md|markdown|json|ya?ml|csv|tsv|log|ini|toml|cfg|conf|xml|html?|css|scss|jsx?|tsx?|mjs|cjs|py|rb|go|rs|java|kt|c|h|cpp|hpp|cs|php|sh|bash|zsh|sql|env|gitignore|dockerfile)$/i

export async function readAttachments(fileList) {
  const out = []
  for (const file of Array.from(fileList)) {
    const name = file.webkitRelativePath || file.name
    const base = { name, size: file.size, type: file.type || '' }

    if (file.type.startsWith('image/')) {
      if (file.size > MAX_IMAGE_BYTES) {
        out.push({ ...base, kind: 'image', error: `too large (max ${MAX_IMAGE_BYTES / 1024 / 1024}MB)` })
        continue
      }
      try {
        // Strip the "data:image/png;base64," prefix -- the backend and the
        // vision models want bare base64.
        const dataUrl = await new Promise((resolve, reject) => {
          const fr = new FileReader()
          fr.onload = () => resolve(fr.result)
          fr.onerror = () => reject(fr.error)
          fr.readAsDataURL(file)
        })
        out.push({ ...base, kind: 'image', b64: String(dataUrl).split(',')[1] || '' })
      } catch {
        out.push({ ...base, kind: 'image', error: 'unreadable' })
      }
      continue
    }

    if (file.type.startsWith('video/')) {
      out.push({ ...base, kind: 'video' })
      continue
    }

    if (TEXT_RE.test(name) || file.type.startsWith('text/')) {
      if (file.size > MAX_TEXT_BYTES) {
        out.push({ ...base, kind: 'text', error: `too large (max ${MAX_TEXT_BYTES / 1024}KB)` })
        continue
      }
      try {
        out.push({ ...base, kind: 'text', content: await file.text() })
      } catch {
        out.push({ ...base, kind: 'text', error: 'unreadable' })
      }
      continue
    }

    out.push({ ...base, kind: 'other' })
  }
  return out
}

/**
 * Strip an attachment down to what's safe to KEEP in chat history.
 *
 * The payloads are for one request, not for storage. A single 2.5MB image
 * becomes ~3.4MB of base64, and chat history lives in localStorage, which
 * gives roughly 5MB for the whole origin -- persisting one would evict the
 * user's entire history (and every project) to save a picture they can
 * already see. Keeps only what the message bubble renders.
 */
export const stripAttachmentPayloads = (attachments = []) =>
  (attachments || []).map(({ name, size, kind, type, error }) => ({
    name, size, kind, type, error,
  }))

/** Split read attachments into what each transport can actually carry. */
export function packAttachments(attachments = []) {
  let imageB64 = ''
  const textParts = []
  const unsupported = []

  for (const a of attachments || []) {
    if (a.kind === 'image' && a.b64 && !imageB64) {
      // One image per turn: the vision path takes a single image_b64, and
      // silently dropping the 2nd..nth would be worse than saying so.
      imageB64 = a.b64
    } else if (a.kind === 'image' && a.b64) {
      unsupported.push(`a second image (${a.name}; only one image per message is sent)`)
    } else if (a.kind === 'text' && a.content) {
      textParts.push(`[Attached file: ${a.name}]\n${a.content}`)
    } else if (a.kind === 'video') {
      unsupported.push(`a video (${a.name})`)
    } else if (a.error) {
      unsupported.push(`${a.name} (${a.error})`)
    } else if (a.kind === 'other') {
      unsupported.push(`${a.name} (unsupported file type)`)
    }
  }

  return { imageB64, textContext: textParts.join('\n\n'), unsupported }
}

/* ── Engine cascade: one fallback order, every caller shares it ────────────
 * Best-answer first:
 *   1. VibeAI API  - the real Manager, direct (localhost:8000, dev only)
 *   2. Team proxy  - the SAME real Manager, reached via our own /api/team on
 *                    the public deploy (Render-hosted)
 *   3. OmniRoute   - single-model fallback via the local gateway
 *                    (localhost:20128)
 *   4. Edge proxy  - single-model fallback via our own /api/chat (works
 *                    anywhere, including the public deploy)
 *   5. simulated   - canned templates, last resort
 * Each failure falls through to the next, the same way the model registry's
 * circuit breaker degrades across providers rather than erroring out.
 *
 * Deliberately conversation-agnostic: it takes a flat `history` array and a
 * `probe` (from useEngineProbe.js) and returns a reply, with no opinion on
 * where the caller stores it. ChatApp animates it into a chat-list entry;
 * ProjectWorkspace's single persistent thread does the same into a flat
 * messages array. The fallback order itself lives in exactly one place
 * either way.
 */
export async function dispatchEngineReply({
  text, history, convId, sent, team, mode, systemPrompt, getToken, probe,
}) {
  // Wait for any in-flight probe before trusting engineRef -- otherwise a
  // message sent right after page load reads the initial `false` defaults
  // instead of the real (still-resolving) availability.
  if (probe.probeRef.current) await probe.probeRef.current
  const engines = probe.engineRef.current

  /* Attachments are unpacked ONCE here rather than inside each tier, so
     every tier benefits: text files get inlined as context for all of them,
     and anything unreadable is declared rather than silently dropped. Only
     the manager tier can carry an image (it reaches the vision team); the
     single-model tiers get told an image was attached instead of pretending
     none was.

     Before this, `sent` was passed in and used ONLY by the simulated
     fallback -- every real tier ignored attachments entirely, which is why
     uploading a file and asking about it produced an answer about nothing. */
  const { imageB64, textContext, unsupported } = packAttachments(sent)
  let prompt = text
  if (textContext) prompt = `${textContext}\n\n---\n${text}`
  const cannotCarryImage = imageB64
    ? ['an image (this engine tier cannot see images)']
    : []

  // Only the manager tier reaches the vision team, so it is the one tier
  // that receives the image itself; the rest are told an image exists.
  const withNotes = (base, notes) =>
    notes.length
      ? `[The user attached ${notes.join(', ')}. Say so plainly instead of ` +
        `guessing at the contents.]

${base}`
      : base

  const managerPrompt = withNotes(prompt, unsupported)
  const blindPrompt = withNotes(prompt, [...unsupported, ...cannotCarryImage])

  if (engines.live) {
    try {
      // Live hits the same /api/prompt as the manager proxy, so it carries
      // the image too -- it is not a blind tier.
      return await respondLive(managerPrompt, convId, team, mode, imageB64)
    } catch (err) {
      console.error('[engine:live] failed, falling through:', err)
      probe.setLive(false)
      probe.engineRef.current.live = false
    }
  }

  if (engines.manager) {
    try {
      const token = await getToken()
      return await respondTeam(managerPrompt, convId, token, systemPrompt, team, mode, imageB64)
    } catch (err) {
      console.error('[engine:manager] failed, falling through:', err)
      probe.setManager(false)
      probe.engineRef.current.manager = false
    }
  }

  if (engines.omni) {
    try {
      return await respondOmni(blindPrompt, history, mode, systemPrompt)
    } catch (err) {
      console.error('[engine:omni] failed, falling through:', err)
      probe.setOmni(false)
      probe.engineRef.current.omni = false
    }
  }

  if (engines.edge) {
    try {
      const token = await getToken()
      return await respondEdge(blindPrompt, history, token, mode, systemPrompt)
    } catch (err) {
      console.error('[engine:edge] failed, falling through:', err)
      probe.setEdge(false)
      probe.engineRef.current.edge = false
    }
  }

  const sim = respond(text, sent, team)
  sim.text = `[CB] no live engine reachable - simulated response\n${sim.text}`
  return sim
}

const STRONG_CODE = [
  'python', 'javascript', 'typescript', 'react', 'html', 'css', 'sql', 'api',
  'cli', 'script', 'function', 'class', 'bug', 'refactor', 'server',
  'landing page', 'website', 'component', 'regex', 'zip',
]
const WEAK_CODE = ['code', 'build', 'create', 'make', 'fix', 'implement', 'app', 'program']
const WRITING = [
  'write', 'draft', 'essay', 'article', 'blog', 'email', 'letter', 'poem',
  'story', 'post', 'caption', 'speech', 'resume', 'cover letter', 'rewrite',
]
const RESEARCH = [
  'research', 'search', 'find out', 'compare', 'investigate', 'sources',
  'latest', 'best free', 'pros and cons', 'market', 'trends', 'analyze',
]
const EXPLAIN = [
  'explain', 'what is', 'what are', 'how does', 'how do', 'why does',
  'why is', 'difference between', 'summarize', 'eli5', 'teach me',
]

const has = (text, hints) => hints.some((h) => text.includes(h))

// A coarse keyword approximation for the SIMULATED (last-resort, every real
// tier unreachable) fallback only -- it does NOT mirror the real classifier.
// The actual router (teams/router_team.py) is an LLM classifying into
// debugging/vibe_coding/ui_design/animation/video_analysis/mixed with
// needs_vision/needs_code/needs_design flags; matching that taxonomy here
// would mean writing new canned reply templates for a path that only runs
// when live/manager/omni/edge are ALL down, so it stays a 5-bucket
// approximation on purpose.
export function classify(text) {
  const t = text.toLowerCase()
  if (has(t, STRONG_CODE)) return 'code'
  if (has(t, RESEARCH)) return 'research'
  if (has(t, WRITING)) return 'writing'
  if (has(t, EXPLAIN)) return 'explain'
  if (has(t, WEAK_CODE)) return 'code'
  return 'general'
}

export function slugify(text) {
  let slug = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  if (slug.length > 32) {
    slug = slug.slice(0, 32)
    // don't cut mid-word
    const cut = slug.lastIndexOf('-')
    if (cut > 12) slug = slug.slice(0, cut)
  }
  return slug || 'vibeai-project'
}

function pythonProject(prompt, slug) {
  return [
    {
      path: 'main.py',
      content: `"""${slug} — generated by VibeAI (demo engine).\n\nTask: ${prompt.trim()}\n"""\n\nimport argparse\n\n\ndef run(name: str) -> str:\n    return f"VibeAI demo scaffold ready, {name}."\n\n\ndef main() -> None:\n    parser = argparse.ArgumentParser(description=__doc__)\n    parser.add_argument("--name", default="world")\n    args = parser.parse_args()\n    print(run(args.name))\n\n\nif __name__ == "__main__":\n    main()\n`,
    },
    {
      path: 'test_main.py',
      content: `from main import run\n\n\ndef test_run():\n    assert "ready" in run("test")\n`,
    },
    {
      path: 'requirements.txt',
      content: '# no third-party dependencies for the scaffold\n',
    },
    {
      path: 'README.md',
      content: `# ${slug}\n\nGenerated by VibeAI's demo engine for the task:\n\n> ${prompt.trim()}\n\n## Run\n\n\`\`\`bash\npython main.py --name you\n\`\`\`\n\n## Test\n\n\`\`\`bash\npython -m pytest -q\n\`\`\`\n`,
    },
  ]
}

function webProject(prompt, slug) {
  return [
    {
      path: 'index.html',
      content: `<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n    <title>${slug}</title>\n    <link rel="stylesheet" href="styles.css" />\n  </head>\n  <body>\n    <main class="card">\n      <h1>${slug}</h1>\n      <p>Scaffold generated by VibeAI for: ${prompt.trim()}</p>\n      <button id="cta">It works</button>\n    </main>\n    <script src="app.js"></script>\n  </body>\n</html>\n`,
    },
    {
      path: 'styles.css',
      content: `:root {\n  --bg: #05070a;\n  --accent: #2be8ff;\n}\n\nbody {\n  margin: 0;\n  min-height: 100vh;\n  display: grid;\n  place-items: center;\n  background: var(--bg);\n  color: #d9f6fb;\n  font-family: system-ui, sans-serif;\n}\n\n.card {\n  text-align: center;\n  padding: 3rem;\n  border: 1px solid rgba(43, 232, 255, 0.25);\n  border-radius: 8px;\n}\n\nbutton {\n  margin-top: 1rem;\n  padding: 0.6rem 1.4rem;\n  background: var(--accent);\n  border: none;\n  border-radius: 4px;\n  font-weight: 600;\n  cursor: pointer;\n}\n`,
    },
    {
      path: 'app.js',
      content: `document.getElementById('cta').addEventListener('click', () => {\n  alert('VibeAI scaffold running.')\n})\n`,
    },
    {
      path: 'README.md',
      content: `# ${slug}\n\nGenerated by VibeAI's demo engine for the task:\n\n> ${prompt.trim()}\n\nOpen \`index.html\` in a browser.\n`,
    },
  ]
}

export function generateFiles(prompt) {
  const t = prompt.toLowerCase()
  const slug = slugify(prompt)
  const isWeb = ['html', 'css', 'website', 'landing', 'page', 'react', 'frontend'].some((h) =>
    t.includes(h),
  )
  return { slug, files: isWeb ? webProject(prompt, slug) : pythonProject(prompt, slug) }
}

export async function makeZip(files, slug) {
  const zip = new JSZip()
  const root = zip.folder(slug)
  for (const f of files) root.file(f.path, f.content)
  return zip.generateAsync({ type: 'blob' })
}

/* ── Projects: the real autonomous agent, not a scaffold ───────────────────
 * Posts to /api/project (api/project.js), which forwards to the backend's
 * POST /api/agent -- core/agent_loop.py, the same loop evals/run_eval.py
 * grades. It writes real files, runs real shell commands and passes a
 * build/test gate before returning, so what comes back is a project that
 * actually ran, not a template.
 *
 * Deliberately NOT falling back to the client-side generateFiles() scaffold
 * on failure. Everywhere else in this app a degraded tier still answers the
 * question, so falling through is right; here the whole promise is "the agent
 * built and verified this", and silently handing over an unverified template
 * under the same "project" label would be the one thing a build surface must
 * never do. A failure is surfaced as a failure.
 */
export async function buildProject(task, token, taskType = 'coding', projectId = null) {
  const res = await fetch('/api/project', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    // projectId scopes the backend's workspace so repeated builds inside the
    // same persistent project land in the same directory instead of every
    // build on every project colliding in the backend's shared default
    // workspace -- see api/project.js.
    body: JSON.stringify({ task, taskType, projectId }),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.error || `project build failed (${res.status})`)
  if (!data?.files?.length) {
    throw new Error('the agent finished but wrote no files')
  }
  return {
    slug: slugify(task),
    summary: data.summary || '',
    files: data.files,
    filesTruncated: !!data.filesTruncated,
    commandsRun: data.commandsRun || [],
    iterations: data.iterations || 0,
    totalMs: data.totalMs || 0,
  }
}

/** Whether the real agent backend is reachable. Projects has no simulated
 *  tier, so this gates the surface instead of selecting a fallback. */
export async function checkProjects() {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 4000)
    const res = await fetch('/api/project', { signal: ctrl.signal })
    clearTimeout(t)
    if (!res.ok) return false
    const data = await res.json()
    return !!data.ok
  } catch {
    return false
  }
}

/* ── Registry: the real roster, never a local copy ─────────────────────────
 * Reads GET /api/registry (api/registry.js -> backend MODEL_REGISTRY). On any
 * failure this returns ok:false with an empty roster rather than falling back
 * to a bundled list: a stale hardcoded roster that still LOOKS right is worse
 * than an honest "unavailable", because the whole point of this surface is
 * that the numbers are sourced from the running system.
 */
export async function fetchRegistry() {
  try {
    const res = await fetch('/api/registry')
    if (!res.ok) return { ok: false, entries: [] }
    const data = await res.json()
    return data?.ok ? data : { ok: false, entries: [], reason: data?.reason }
  } catch {
    return { ok: false, entries: [] }
  }
}

/** Build the zip and hand it to the browser as a download. */
export async function downloadProject(project) {
  const blob = await makeZip(project.files, project.slug)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${project.slug}.zip`
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoking immediately can cancel the download in some browsers; one frame
  // is enough for the click to be consumed.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/* ── Project memory ────────────────────────────────────────────────────────
 * Rolling summary of what a project's conversation established, folded back
 * into the system prompt so a long-running project doesn't have to re-explain
 * itself every time the message history scrolls past the context window.
 *
 * Deliberately routed to a RAW completion (omni locally, /api/chat on the
 * deploy) rather than through dispatchEngineReply: the manager/live tiers run
 * the full multi-agent council, and spending a five-model pipeline on "write
 * two bullet points about this chat" would cost more than the conversation it
 * summarizes. `fast` mode for the same reason.
 *
 * Honest limitation: this needs the omni or edge tier. On a deployment where
 * only the manager backend is reachable, memory silently stops updating --
 * the caller treats a failure as "leave memory as it was", never as an error
 * worth interrupting the user for, because a missing summary degrades quality
 * slightly while a thrown error would break the send path entirely.
 */
const MEMORY_SYSTEM =
  'You maintain a compact memory for a long-running project. Given the ' +
  'existing memory and new conversation turns, return an UPDATED memory: a ' +
  'short list of durable facts worth remembering across future chats — ' +
  'decisions made, constraints, stack/tooling choices, naming, preferences, ' +
  'and open threads.\n\n' +
  'Rules:\n' +
  '- Keep it under 200 words. Merge and compress; do not just append.\n' +
  '- Facts only. No pleasantries, no meta-commentary, no "the user asked".\n' +
  '- Drop anything already superseded by a later turn.\n' +
  '- Omit one-off trivia and anything already obvious from the project name.\n' +
  '- Output the memory itself as plain lines starting with "- ". Nothing else.\n' +
  '- If there is nothing durable worth keeping, output exactly: NONE'

async function completeRaw({ system, prompt, mode = 'fast', token, probe }) {
  const engines = probe?.engineRef?.current || {}
  const cfg = modeConfig(mode)

  if (engines.omni) {
    try {
      const { text } = await omniComplete(
        [
          { role: 'system', content: system },
          { role: 'user', content: prompt },
        ],
        { model: cfg.omniModel, maxTokens: cfg.maxTokens },
      )
      return text
    } catch {
      /* fall through to the edge proxy */
    }
  }

  if (engines.edge) {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        mode,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: prompt },
        ],
      }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data?.error || `edge proxy ${res.status}`)
    return data.text
  }

  throw new Error('no raw-completion engine reachable')
}

/**
 * Fold `newMessages` into `existingMemory`. Returns the updated memory string,
 * or '' when the model judges there's nothing durable worth keeping.
 */
export async function summarizeMemory({ existingMemory, newMessages, token, probe }) {
  const transcript = newMessages
    .filter((m) => m.content?.trim())
    .map((m) => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.content.slice(0, 2000)}`)
    .join('\n\n')
  if (!transcript.trim()) return existingMemory || ''

  const prompt =
    `EXISTING MEMORY:\n${existingMemory || '(none yet)'}\n\n` +
    `NEW TURNS:\n${transcript}\n\n` +
    'Return the updated memory.'

  const text = await completeRaw({ system: MEMORY_SYSTEM, prompt, mode: 'fast', token, probe })
  const clean = (text || '').trim()
  if (!clean || clean === 'NONE') return ''
  return clean
}

const TEAM_LABEL = {
  auto: 'auto-routed',
  code: 'team.code',
  brain: 'team.brain',
  vision: 'team.vision',
  design: 'team.design',
}

const DEMO_NOTE = '*(demo engine — connect the local VibeAI API for real multi-model runs)*'

// Strip the leading command verb so headings read as a topic, not an order.
function topicOf(prompt) {
  const topic = prompt
    .replace(
      /^(please\s+)?(can you\s+|could you\s+)?(write|draft|research|search|find out|find|explain|summarize|analyze|compare|investigate|create|make|build|give me|tell me about|what is|what are|how does|how do|why)\s+(a|an|the|about)?\s*/i,
      '',
    )
    .replace(/[?.!]+$/, '')
    .trim()
  return topic || prompt.trim()
}

function codeReply(prompt, team, attachNote) {
  const { slug, files } = generateFiles(prompt)
  const fileList = files.map((f) => `- \`${slug}/${f.path}\``).join('\n')
  const text =
    `[ROUTER] intent: code — dispatched to ${team === 'auto' ? 'team.code' : TEAM_LABEL[team]}\n` +
    `[COUNCIL] plan → draft → critique → refine → synthesize ✓\n` +
    `[VERIFY] battery pass — 0 broken imports · 0 placeholder stubs${attachNote}\n` +
    `Here's a working scaffold for your task.\n\n` +
    `**Files generated**\n${fileList}\n\n` +
    `Everything is packaged below — download the zip, unzip it, and run it locally. ` +
    `The README inside has the exact commands.\n\n${DEMO_NOTE}`
  return { text, project: { slug, files } }
}

function researchReply(prompt, attachNote) {
  const topic = topicOf(prompt)
  const text =
    `[ROUTER] intent: research — dispatched to team.brain\n` +
    `[COUNCIL] decomposing question · querying models in parallel · cross-checking claims ✓${attachNote}\n` +
    `## Research brief — ${topic}\n\n` +
    `**How the live pipeline runs this**\n` +
    `Several free-tier models research the question in parallel, then the Council ` +
    `cross-checks their claims against each other before synthesis — disagreements get ` +
    `flagged, not averaged away.\n\n` +
    `**Working outline it follows**\n` +
    `- Frame the core question: ${topic}\n` +
    `- Split into sub-questions — definitions, current landscape, trade-offs, open problems\n` +
    `- Gather from multiple models plus any attached context, tagging each claim's confidence\n` +
    `- Critique pass: hunt contradictions and unsupported claims\n` +
    `- Synthesize: findings ranked by confidence, caveats attached\n\n` +
    `This hosted demo can't fetch live sources, so you get the method instead of invented ` +
    `facts — the real system fills this outline with cross-checked findings.\n\n${DEMO_NOTE}`
  return { text, project: null }
}

function writingReply(prompt, attachNote) {
  const topic = topicOf(prompt)
  const text =
    `[ROUTER] intent: writing — dispatched to the Manager Council\n` +
    `[COUNCIL] plan → draft → critique → refine → synthesize ✓${attachNote}\n` +
    `Here's the working structure for **${topic}**:\n\n` +
    `- **Hook** — open with the sharpest fact or tension in the piece\n` +
    `- **Context** — two or three sentences on why this matters now\n` +
    `- **Core** — three points, one paragraph each; lead with the claim, follow with evidence\n` +
    `- **Close** — return to the hook and land one clear takeaway or call to action\n\n` +
    `On the live system the Council drafts this in full, a second model attacks the draft ` +
    `in Critique, and you receive the refined final — no single model's first attempt ` +
    `survives untouched.\n\n${DEMO_NOTE}`
  return { text, project: null }
}

function explainReply(prompt, attachNote) {
  const topic = topicOf(prompt)
  const text =
    `[ROUTER] intent: reasoning — dispatched to team.brain\n` +
    `[COUNCIL] 5-stage pipeline complete ✓${attachNote}\n` +
    `**${topic}** — how the live system breaks an explanation down:\n\n` +
    `- **Definition** — pin the term precisely before reasoning about it\n` +
    `- **Mechanism** — walk cause to effect, step by step\n` +
    `- **Example** — one concrete case that makes it stick\n` +
    `- **Pitfalls** — where the naive understanding goes wrong\n\n` +
    `The Critique stage exists exactly for answers like this: a different model attacks ` +
    `the draft for hand-waving before you ever see it.\n\n${DEMO_NOTE}`
  return { text, project: null }
}

function generalReply(attachNote) {
  const text =
    `[ROUTER] intent: general — Manager evaluating task ✓${attachNote}\n` +
    `The Manager routes every request — searching, research, writing, coding, image ` +
    `understanding — to a specialist team, and new models from any provider slot into the ` +
    `registry as (model × provider × role) entries, so capability grows without rewiring.\n\n` +
    `This hosted site runs a simulated engine, because the actual VibeAI API executes code ` +
    `and shell commands and therefore binds to localhost only. Try a **coding task** for the ` +
    `full flow — generated files, verifier output, downloadable zip — or a **research**, ` +
    `**writing**, or **explain** request to see how each intent routes.\n\n${DEMO_NOTE}`
  return { text, project: null }
}

/* ── image generation ──────────────────────────────────────────────────────
 * Pollinations needs no API key and sends `Access-Control-Allow-Origin: *`,
 * so the browser can generate images directly. That matters here: the hosted
 * site can never reach the real VibeAI API (it executes shell commands and
 * binds to localhost by design), yet image generation still works fully in
 * BOTH the live and simulated engines. Verified live 2026-07-28: HTTP 200,
 * image/jpeg, ~89KB, but ~17s to first byte -- slow enough that the WAIT is
 * the real design problem, which is why Message.jsx renders a progressive
 * "render bay" instead of a spinner.
 */
const IMAGE_VERBS = /\b(draw|paint|render|generate|create|make|design|imagine|visuali[sz]e)\b/i
const IMAGE_NOUNS = /\b(image|picture|photo|art|artwork|illustration|poster|wallpaper|logo|icon|banner|scene|portrait|painting|render|concept art|mockup)\b/i

export function isImageRequest(text) {
  const t = text || ''
  // Require BOTH a verb and an image noun. "create a landing page" must stay
  // a coding task, and "explain how image compression works" must stay an
  // explanation -- neither should silently become an image generation.
  if (!IMAGE_NOUNS.test(t)) return false
  if (/\b(landing page|website|component|app|api|script|function|zip)\b/i.test(t)) return false
  return IMAGE_VERBS.test(t) || /^\s*(an?|the)\s+.*\b(image|picture|art|poster)\b/i.test(t)
}

/** Strip the instruction wrapper so the model receives the subject, not "draw me a". */
export function imageSubject(text) {
  const subject = (text || '')
    // `me` is stripped here, before the anchored noun rule below, so
    // "draw me a picture of X" collapses to "X" rather than "picture X".
    .replace(/\b(please|can you|could you|for me|me)\b/gi, ' ')
    .replace(new RegExp(`\\b(${IMAGE_VERBS.source.slice(2, -2)})\\b`, 'gi'), ' ')
    // Drop a LEADING image noun only ("an image of a neon city" -> "neon
    // city"). Anchored, because the noun can be the actual subject further
    // in: "a poster of a movie poster" must keep the second one, and
    // "portrait of a woman" must not lose "portrait" if it leads meaningfully.
    .replace(/^\W*(an?|the)?\s*(image|picture|photo|artwork|illustration|render)\s+(of|showing)?\s*/i, ' ')
    .replace(/\b(an?|the|me|of)\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return subject || text
}

/* Deterministic 32-bit hash (djb2). Same prompt -> same render signature, so a
 * given image always develops the same way. This is what makes the reveal feel
 * authored rather than a canned animation replayed identically for everything,
 * without resorting to Math.random() (which would also break React re-renders). */
export function promptSeed(text) {
  let h = 5381
  for (let i = 0; i < (text || '').length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0
  return h
}

/** Per-image choreography derived from the prompt. Drives CSS custom props. */
export function renderSignature(prompt) {
  const seed = promptSeed(prompt)
  const AXES = ['tb', 'bt', 'lr', 'rl']
  // NOTE: `>>>` (unsigned), never `>>`. djb2 routinely produces values above
  // 2^31, where the signed shift yields a negative number and JS keeps the
  // sign through `%`. Caught in testing: `>>` gave sweepMs 917 (floor is
  // 1500), revealMs 244 (floor is 900) and bracket -2, which rendered as the
  // dead CSS class `bracket--2`.
  return {
    seed,
    axis: AXES[seed % AXES.length],
    grain: 2 + (seed % 3),                     // lattice density 2..4
    sweepMs: 1500 + ((seed >>> 3) % 900),      // scan cadence 1.5s..2.4s
    revealMs: 900 + ((seed >>> 7) % 700),      // resolve duration 0.9s..1.6s
    bracket: (seed >>> 11) % 3,                // corner-bracket variant 0..2
  }
}

export function buildImage(prompt, { width = 1024, height = 576 } = {}) {
  const subject = imageSubject(prompt)
  const seed = promptSeed(prompt)
  const url =
    `https://image.pollinations.ai/prompt/${encodeURIComponent(subject)}` +
    `?width=${width}&height=${height}&nologo=true&seed=${seed}`
  return { url, subject, width, height, sig: renderSignature(prompt) }
}

function imageReply(prompt, attachNote) {
  const image = buildImage(prompt)
  const text =
    `[ROUTER] intent classified: image -> dispatch team.design${attachNote}` +
    `Rendering **${image.subject}** at ${image.width}x${image.height}.\n\n` +
    `The design team uses a keyless free model, so this runs entirely in your ` +
    `browser. No API key, no server round trip.`
  return { text, project: null, image }
}

export function respond(prompt, attachments, team = 'auto') {
  const attachNote = attachments.length
    ? `\n[CONTEXT] ${attachments.length} attachment${attachments.length > 1 ? 's' : ''} received: ${attachments
        .slice(0, 3)
        .map((a) => a.name)
        .join(', ')}${attachments.length > 3 ? '…' : ''}\n`
    : '\n'

  // Checked before classify(): an image request often contains coding-ish
  // verbs ("create", "make", "design") that would otherwise route it to the
  // code engine and return a zip instead of a picture.
  if (isImageRequest(prompt)) return imageReply(prompt, attachNote)

  switch (classify(prompt)) {
    case 'code':
      return codeReply(prompt, team, attachNote)
    case 'research':
      return researchReply(prompt, attachNote)
    case 'writing':
      return writingReply(prompt, attachNote)
    case 'explain':
      return explainReply(prompt, attachNote)
    default:
      return generalReply(attachNote)
  }
}
