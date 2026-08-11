async function request(body, query = '') {
  const response = await fetch(`/api/capabilities${query}`, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'Capability service is unavailable')
  return data
}

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
}

async function actionRequest(body, query = '') {
  const response = await fetch(`/api/actions${query}`, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'Action inbox is unavailable')
  return data
}

export const actionApi = {
  pending: () => actionRequest(null),
  detail: (id) => actionRequest(null, `?id=${encodeURIComponent(id)}`),
  approve: (id, requestDigest) =>
    actionRequest({ operation: 'approve', id, request_digest: requestDigest }),
  deny: (id) => actionRequest({ operation: 'deny', id }),
}

export async function integrationRequest(operation, extra = {}) {
  const response = await fetch('/api/integrations', {
    method: operation === 'list' ? 'GET' : 'POST',
    headers: operation === 'list' ? undefined : { 'Content-Type': 'application/json' },
    body: operation === 'list' ? undefined : JSON.stringify({ operation, ...extra }),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'Integration service is unavailable')
  return data
}
