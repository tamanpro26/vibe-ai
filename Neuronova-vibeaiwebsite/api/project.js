import { requireSessionContext } from './_lib/clerkAuth.js'
import { boundedHistory } from './_lib/requestData.js'
import { createHash } from 'node:crypto'

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
 * Real agent runs take minutes, so POST starts a job on the persistent backend
 * and returns quickly. Authenticated GET requests poll that job in short-lived
 * serverless invocations; no single Vercel request has to survive the build.
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
const JOB_ID_RE = /^[a-f0-9]{32}$/

function browserResult(data) {
  return {
    summary: data.final_response,
    files: data.files || [],
    filesTruncated: !!data.files_truncated,
    commandsRun: data.commands_run || [],
    iterations: data.iterations ?? 0,
    totalMs: data.total_ms ?? 0,
  }
}

export default async function handler(req, res) {
  const jobId = typeof req.query?.jobId === 'string' ? req.query.jobId : ''
  if (req.method === 'GET' && !jobId) {
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
  if (req.method !== 'POST' && !(req.method === 'GET' && jobId)) {
    res.status(405).json({ error: 'method not allowed' })
    return
  }
  if (!VIBE_BACKEND_URL || !VIBE_API_TOKEN) {
    res.status(500).json({ error: 'server misconfigured: missing VIBE_BACKEND_URL or VIBE_API_TOKEN' })
    return
  }

  let session
  let userToken
  try {
    const context = await requireSessionContext(req)
    session = context.payload
    userToken = context.token
  } catch {
    res.status(401).json({ error: 'sign in required' })
    return
  }

  if (req.method === 'GET') {
    if (!JOB_ID_RE.test(jobId)) {
      res.status(400).json({ error: 'invalid job id' })
      return
    }
    try {
      const upstream = await fetch(`${VIBE_BACKEND_URL}/api/agent/jobs/${jobId}`, {
        headers: {
          Authorization: `Bearer ${VIBE_API_TOKEN}`,
          'X-Vibe-User-Token': userToken,
        },
      })
      const data = await upstream.json()
      if (!upstream.ok) {
        res.status(upstream.status).json({ error: data?.detail || 'upstream error' })
        return
      }
      if (data.status === 'failed') {
        res.status(502).json({ error: data.error || 'agent build failed' })
        return
      }
      if (data.status !== 'completed') {
        res.status(200).json({ status: 'running', jobId })
        return
      }
      res.status(200).json({ status: 'completed', ...browserResult(data.result || {}) })
    } catch (err) {
      res.status(502).json({ error: String(err?.message || err) })
    }
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
  const userKey = createHash('sha256').update(String(session.sub || 'unknown')).digest('hex').slice(0, 20)
  const workspace = PROJECT_ID_RE.test(projectId) ? `projects/${userKey}/${projectId}` : undefined
  const context = typeof req.body?.context === 'string' ? req.body.context.slice(0, 48_000) : ''
  const history = boundedHistory(req.body?.history)

  try {
    const upstream = await fetch(`${VIBE_BACKEND_URL}/api/agent/jobs`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${VIBE_API_TOKEN}`,
        'Content-Type': 'application/json',
        'X-Vibe-User-Token': userToken,
      },
      body: JSON.stringify({
        task,
        task_type: taskType,
        include_files: true,
        workspace,
        context,
        history,
      }),
    })
    const data = await upstream.json()
    if (!upstream.ok) {
      res.status(upstream.status).json({ error: data?.detail || 'upstream error' })
      return
    }
    res.status(202).json({ status: 'running', jobId: data.job_id })
  } catch (err) {
    res.status(502).json({ error: String(err?.message || err) })
  }
}
