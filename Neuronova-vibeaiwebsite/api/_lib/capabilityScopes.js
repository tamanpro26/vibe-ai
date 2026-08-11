import { SAFE_ID } from './vibeProxy.js'

async function registerScope(fetchImpl, backendUrl, apiToken, userToken, kind, id, parentId) {
  const response = await fetchImpl(
    `${backendUrl}/api/capabilities/scopes/${kind}/${encodeURIComponent(id)}`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiToken}`,
        'Content-Type': 'application/json',
        'X-Vibe-User-Token': userToken,
      },
      body: JSON.stringify({ parent_scope_id: parentId || null }),
    },
  )
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data?.detail || `could not register ${kind} capability scope`)
  }
}

export async function registerCapabilityScopes({
  backendUrl,
  apiToken,
  userToken,
  projectId,
  chatId,
  fetchImpl = fetch,
}) {
  const project = SAFE_ID.test(projectId || '') ? projectId : null
  const chat = SAFE_ID.test(chatId || '') ? chatId : null
  if (projectId && !project) throw new Error('invalid project id')
  if (chatId && !chat) throw new Error('invalid chat id')
  if (project) {
    await registerScope(fetchImpl, backendUrl, apiToken, userToken, 'project', project, null)
  }
  if (chat) {
    await registerScope(fetchImpl, backendUrl, apiToken, userToken, 'chat', chat, project)
  }
  return { projectId: project, chatId: chat }
}
