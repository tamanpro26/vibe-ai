import { forwardVibe, SAFE_ID } from './_lib/vibeProxy.js'

export default async function handler(req, res) {
  const operation = req.method === 'GET' ? (req.query?.id ? 'detail' : 'pending') : req.body?.operation
  const rawId = req.method === 'GET' ? req.query?.id : req.body?.id
  const id = typeof rawId === 'string' && SAFE_ID.test(rawId) ? rawId : null
  let route
  if (operation === 'pending') route = { method: 'GET', path: '/api/actions/pending' }
  if (operation === 'detail' && id) route = { method: 'GET', path: `/api/actions/${id}` }
  if (operation === 'approve' && id) route = {
    method: 'POST',
    path: `/api/actions/${id}/approve`,
    body: { request_digest: req.body?.request_digest },
  }
  if (operation === 'deny' && id) route = { method: 'POST', path: `/api/actions/${id}/deny`, body: {} }
  if (!route) {
    res.status(400).json({ error: 'unsupported action operation or invalid identifier' })
    return
  }
  await forwardVibe(req, res, route)
}
