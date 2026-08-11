import { forwardVibe, SAFE_ID } from './_lib/vibeProxy.js'

export default async function handler(req, res) {
  const operation = req.method === 'GET' ? 'pending' : req.body?.operation
  const id = typeof req.body?.id === 'string' && SAFE_ID.test(req.body.id) ? req.body.id : null
  let route
  if (operation === 'pending') route = { method: 'GET', path: '/api/actions/pending' }
  if (operation === 'approve' && id) route = { method: 'POST', path: `/api/actions/${id}/approve` }
  if (operation === 'deny' && id) route = { method: 'POST', path: `/api/actions/${id}/deny` }
  if (!route) {
    res.status(400).json({ error: 'unsupported action operation or invalid identifier' })
    return
  }
  await forwardVibe(req, res, route)
}
