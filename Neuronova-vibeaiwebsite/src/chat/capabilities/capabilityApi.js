async function apiRequest(path, body, fallback) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || fallback)
  return data
}

const request = (body, query = '') =>
  apiRequest(`/api/capabilities${query}`, body, 'Capability service is unavailable')

export const capabilityApi = {
  list: () => request(null),
  install: (id) => request({ operation: 'install', id }),
  setActivation: (mode) => request({ operation: 'activation', mode, onboarding_accepted: true }),
  createDraft: (manifest) => request({ operation: 'create_draft', manifest }),
  publish: (id) => request({ operation: 'publish', id }),
  importGitHub: (provider, repository, commitSha) =>
    request({ operation: 'import_github', provider, repository, commit_sha: commitSha }),
  setScope: (id, scopeKind, scopeId, state) =>
    request({ operation: 'set_scope', id, scope_kind: scopeKind, scope_id: scopeId, state }),
  registerScope: (scopeKind, scopeId, parentScopeId = null) =>
    request({
      operation: 'register_scope',
      scope_kind: scopeKind,
      scope_id: scopeId,
      parent_scope_id: parentScopeId,
    }),
}

const actionRequest = (body, query = '') =>
  apiRequest(`/api/actions${query}`, body, 'Action inbox is unavailable')

export const actionApi = {
  propose: (proposal) => actionRequest({ operation: 'propose', proposal }),
  pending: () => actionRequest(null),
  detail: (id) => actionRequest(null, `?id=${encodeURIComponent(id)}`),
  approve: (id, requestDigest) =>
    actionRequest({ operation: 'approve', id, request_digest: requestDigest }),
  deny: (id) => actionRequest({ operation: 'deny', id }),
}

export async function integrationRequest(operation, extra = {}) {
  return apiRequest(
    '/api/integrations',
    operation === 'list' ? null : { operation, ...extra },
    'Integration service is unavailable',
  )
}
