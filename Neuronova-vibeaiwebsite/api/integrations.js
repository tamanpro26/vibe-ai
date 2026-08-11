import { forwardVibe, SAFE_ID } from './_lib/vibeProxy.js'

export default async function handler(req, res) {
  const operation = req.method === 'GET' ? 'list' : req.body?.operation
  const id = typeof req.body?.id === 'string' && SAFE_ID.test(req.body.id) ? req.body.id : null
  let route
  if (operation === 'list') route = { method: 'GET', path: '/api/integrations' }
  if (operation === 'connect_github') route = { method: 'POST', path: '/api/integrations/github/connect' }
  if (operation === 'finish_github') {
    route = {
      method: 'POST',
      path: '/api/integrations/github/callback',
      body: {
        state: req.body?.state,
        installation_id: req.body?.installation_id,
        code: req.body?.code,
      },
    }
  }
  if (operation === 'revoke' && id) route = { method: 'POST', path: `/api/integrations/${id}/revoke` }
  if (!route) {
    res.status(400).json({ error: 'unsupported integration operation or invalid identifier' })
    return
  }
  await forwardVibe(req, res, route)
}
