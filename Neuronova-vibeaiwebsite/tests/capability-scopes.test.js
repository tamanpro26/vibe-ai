import assert from 'node:assert/strict'
import test from 'node:test'

import { registerCapabilityScopes } from '../api/_lib/capabilityScopes.js'

test('registers a project before its child chat with verified user context', async () => {
  const calls = []
  const fetchImpl = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({}) }
  }

  const result = await registerCapabilityScopes({
    backendUrl: 'https://backend.test',
    apiToken: 'service-token',
    userToken: 'user-token',
    projectId: 'project-1',
    chatId: 'chat-1',
    fetchImpl,
  })

  assert.deepEqual(result, { projectId: 'project-1', chatId: 'chat-1' })
  assert.equal(calls.length, 2)
  assert.match(calls[0].url, /scopes\/project\/project-1$/)
  assert.deepEqual(JSON.parse(calls[0].options.body), { parent_scope_id: null })
  assert.match(calls[1].url, /scopes\/chat\/chat-1$/)
  assert.deepEqual(JSON.parse(calls[1].options.body), { parent_scope_id: 'project-1' })
  assert.equal(calls[1].options.headers['X-Vibe-User-Token'], 'user-token')
})

test('supports account-level chats and rejects unsafe identifiers', async () => {
  const calls = []
  const fetchImpl = async (url) => {
    calls.push(url)
    return { ok: true, json: async () => ({}) }
  }
  await registerCapabilityScopes({
    backendUrl: 'https://backend.test',
    apiToken: 'service-token',
    userToken: 'user-token',
    chatId: 'chat-2',
    fetchImpl,
  })
  assert.equal(calls.length, 1)
  assert.match(calls[0], /scopes\/chat\/chat-2$/)

  await assert.rejects(
    registerCapabilityScopes({
      backendUrl: 'https://backend.test',
      apiToken: 'service-token',
      userToken: 'user-token',
      projectId: '../escape',
      fetchImpl,
    }),
    /invalid project id/,
  )
})
