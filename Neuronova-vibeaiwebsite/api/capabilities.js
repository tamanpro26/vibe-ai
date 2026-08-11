import { forwardVibe, SAFE_ID } from './_lib/vibeProxy.js'

const SCOPE_KINDS = new Set(['project', 'chat'])

export default async function handler(req, res) {
  const operation = req.method === 'GET' ? 'list' : req.body?.operation
  const id = typeof req.body?.id === 'string' && SAFE_ID.test(req.body.id) ? req.body.id : null
  let route
  switch (operation) {
    case 'list':
      route = { method: 'GET', path: '/api/capabilities' }
      break
    case 'validate':
      route = { method: 'POST', path: '/api/capabilities/validate', body: { manifest: req.body?.manifest } }
      break
    case 'create_draft':
      route = { method: 'POST', path: '/api/capabilities/drafts', body: { manifest: req.body?.manifest } }
      break
    case 'publish':
      if (id) route = { method: 'POST', path: `/api/capabilities/drafts/${id}/publish` }
      break
    case 'delete_draft':
      if (id) route = { method: 'DELETE', path: `/api/capabilities/drafts/${id}` }
      break
    case 'get_version':
      if (id) route = { method: 'GET', path: `/api/capabilities/versions/${id}` }
      break
    case 'archive':
      if (id) route = { method: 'POST', path: `/api/capabilities/versions/${id}/archive` }
      break
    case 'install':
      if (id) route = { method: 'POST', path: `/api/capabilities/versions/${id}/install` }
      break
    case 'activation':
      route = {
        method: 'PATCH',
        path: '/api/capabilities/activation',
        body: { mode: req.body?.mode, onboarding_accepted: !!req.body?.onboarding_accepted },
      }
      break
    case 'register_scope': {
      const kind = req.body?.scope_kind
      const scopeId = req.body?.scope_id
      if (SCOPE_KINDS.has(kind) && SAFE_ID.test(scopeId || '')) {
        route = {
          method: 'POST',
          path: `/api/capabilities/scopes/${kind}/${scopeId}`,
          body: { parent_scope_id: req.body?.parent_scope_id },
        }
      }
      break
    }
    case 'delete_scope': {
      const kind = req.body?.scope_kind
      const scopeId = req.body?.scope_id
      if (SCOPE_KINDS.has(kind) && SAFE_ID.test(scopeId || '')) {
        route = { method: 'DELETE', path: `/api/capabilities/scopes/${kind}/${scopeId}` }
      }
      break
    }
    case 'set_scope': {
      const kind = req.body?.scope_kind
      const scopeId = req.body?.scope_id
      if (id && SCOPE_KINDS.has(kind) && SAFE_ID.test(scopeId || '')) {
        route = {
          method: 'PATCH',
          path: `/api/capabilities/installations/${id}/scopes/${kind}/${scopeId}`,
          body: {
            state: req.body?.state,
            configuration_patch: req.body?.configuration_patch || {},
          },
        }
      }
      break
    }
  }
  if (!route) {
    res.status(400).json({ error: 'unsupported capability operation or invalid identifier' })
    return
  }
  await forwardVibe(req, res, route)
}
