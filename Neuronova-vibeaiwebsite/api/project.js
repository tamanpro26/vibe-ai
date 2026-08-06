import { requireSession } from './_lib/clerkAuth.js'

/*
 * Proxy to the REAL autonomous coding agent (core/agent_loop.py via the
 * backend's POST /api/agent) for the Projects surface.
 *
 * This is a different thing from api/team.js: team.js asks the Manager for an
 * ANSWER, this asks the agent to BUILD something -- it writes real files, runs
 * real shell commands, and self-verifies with a build/test gate before it hands
 * anything back. `include_files: true` makes the backend return each written
 * file's text alongside its path, because the browser cannot read the server's
 * disk and needs the bodies to assemble a zip.
 *
 * Same Clerk gate and same env vars as api/team.js, for a stronger reason: this
 * endpoint's whole purpose is remote code execution on our server. An
 * unauthenticated public route in front of it would be an open RCE proxy.
 *
 * TIMEOUT, stated plainly: real agent runs take minutes -- the recorded eval
 * runs in evals/runs/ range from 2 to 11 iterations. maxDuration below asks
 * Vercel for the longest window it allows, but a long build CAN still outlive
 * it. The backend process keeps working when that happens (it is a persistent
 * Render process, not serverless); it is only this connection that dies, so the
 * user loses the result rather than the work. Making that never happen needs a
 * job id + polling on the backend, which is a bigger change than this route.
 */

export const config = { maxDuration: 300 }

const VIBE_BACKEND_URL = process.env.VIBE_BACKEND_URL || ''
const VIBE_API_TOKEN = process.env.VIBE_API_TOKEN || ''

// Mirrors the backend's own AgentRequest.task_type values.
const TASK_TYPES = new Set(['coding', 'reasoning', 'creative'])

// projectId becomes a path segment in the backend's workspace directory
// (AgentRequest.workspace, api/server.py). It's client-supplied, so it's
// validated here, not trusted -- our own projectStore.js only ever generates
// crypto.randomUUID() values, but this endpoint is reachable by anyone with a
// valid Clerk session, and an unvalidated value becoming part of a filesystem
// path server-side is a path-traversal risk (`../../etc`). Anything that
// doesn't look like a UUID is dropped rather than forwarded -- degrading to
// the shared default workspace is safe, forwarding an unchecked path is not.
const PROJECT_ID_RE = /^[a-zA-Z0-9-]{1,64}$/

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

  const task = typeof req.body?.task === 'string' ? req.body.task.trim() : ''
  if (!task) {
    res.status(400).json({ error: 'task required' })
    return
  }

  // task_type is whitelisted rather than forwarded: it selects a server-side
  // prompt/tool profile, so an arbitrary string from the client has no business
  // reaching the agent loop.
  const taskType = TASK_TYPES.has(req.body?.taskType) ? req.body.taskType : 'coding'

  const projectId = typeof req.body?.projectId === 'string' ? req.body.projectId : ''
  const workspace = PROJECT_ID_RE.test(projectId) ? `projects/${projectId}` : undefined

  try {
    const upstream = await fetch(`${VIBE_BACKEND_URL}/api/agent`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${VIBE_API_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ task, task_type: taskType, include_files: true, workspace }),
    })
    const data = await upstream.json()
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: data?.detail || 'upstream error' })
      return
    }
    res.status(200).json({
      summary: data.final_response,
      files: data.files || [],
      filesTruncated: !!data.files_truncated,
      filesCreated: data.files_created || [],
      filesEdited: data.files_edited || [],
      commandsRun: data.commands_run || [],
      iterations: data.iterations ?? 0,
      totalMs: data.total_ms ?? 0,
    })
  } catch (err) {
    res.status(502).json({ error: String(err?.message || err) })
  }
}
