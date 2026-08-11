import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildProject,
  projectFilesContext,
  packAttachments,
  persistableImage,
  respondEdge,
  respondGeneratedImage,
  respondTeam,
  stripAttachmentPayloads,
  webSearchSources,
} from '../src/chat/engine.js'
import { mergeProjectFiles } from '../src/chat/projectStore.js'

test('respondTeam forwards bounded conversation history and reports Manager provenance', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let request
  globalThis.fetch = async (_url, options) => {
    request = JSON.parse(options.body)
    return new Response(JSON.stringify({
      text: 'Council answer',
      capability_snapshot: { snapshot_id: 'sha256:test' },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const history = [
    { role: 'user', content: 'My preferred stack is React.' },
    { role: 'assistant', content: 'Understood.' },
  ]
  const result = await respondTeam('What stack did I choose?', {
    sessionId: 'session-1', token: 'token', history,
    projectId: 'project-1', chatId: 'chat-1', capabilityIds: ['research-analyst'],
  })

  assert.deepEqual(request.history, history)
  assert.equal(request.project_id, 'project-1')
  assert.equal(request.chat_id, 'chat-1')
  assert.deepEqual(request.capability_ids, ['research-analyst'])
  assert.equal(result.capabilitySnapshot.snapshot_id, 'sha256:test')
  assert.equal(result.provenance, 'VibeAI Council · multi-agent verified')
})

test('respondTeam rejects an empty successful response so the fallback chain can continue', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  globalThis.fetch = async () => new Response(JSON.stringify({ text: '   ' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

  await assert.rejects(
    () => respondTeam('Hello', { sessionId: 'session-1', token: 'token' }),
    /empty response/i,
  )
})

test('webSearchSources authenticates the deployed research proxy', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let authorization
  globalThis.fetch = async (_url, options) => {
    authorization = options.headers.Authorization
    return new Response(JSON.stringify({ sources: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  await webSearchSources('current topic', 'session-token')
  assert.equal(authorization, 'Bearer session-token')
})

test('research on the cloud fallback stays grounded and returns structured sources', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options })
    if (url === '/api/research') {
      return new Response(JSON.stringify({
        sources: [{
          title: 'Primary source',
          extract: 'Verified current fact.',
          url: 'https://example.com/fact',
          site: 'example.com',
        }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url === '/api/chat') {
      return new Response(JSON.stringify({ text: 'Grounded answer', truncated: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    throw new Error(`unexpected request: ${url}`)
  }

  const result = await respondEdge('research the current topic', [], 'session-token')

  assert.equal(result.text, 'Grounded answer')
  assert.equal(result.sources[0].title, 'Primary source')
  assert.equal(calls[1].url, '/api/chat')
  assert.match(JSON.parse(calls[1].options.body).messages.at(-1).content, /Verified current fact/)
})

test('projectFilesContext includes real file bodies and stays within its prompt budget', () => {
  const context = projectFilesContext([
    { path: 'src/App.jsx', content: 'export default function App() { return <main>Hello</main> }' },
    { path: 'README.md', content: 'x'.repeat(40_000) },
  ], 2_000)

  assert.match(context, /src\/App\.jsx/)
  assert.match(context, /return <main>Hello<\/main>/)
  assert.ok(context.length <= 2_000)
})

test('mergeProjectFiles replaces paths in place and appends new files', () => {
  const merged = mergeProjectFiles(
    [{ path: 'a.js', content: 'old' }, { path: 'b.js', content: 'keep' }],
    [{ path: 'a.js', content: 'new' }, { path: 'c.js', content: 'add' }],
  )
  assert.deepEqual(merged.map((file) => [file.path, file.content]), [
    ['a.js', 'new'], ['b.js', 'keep'], ['c.js', 'add'],
  ])
})

test('buildProject forwards project context and recent chat history', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let request
  globalThis.fetch = async (_url, options) => {
    request = JSON.parse(options.body)
    return new Response(JSON.stringify({
      files: [{ path: 'index.html', content: '<h1>Done</h1>' }],
      summary: 'Built',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  await buildProject('Improve it', 'token', 'coding', 'project-1', {
    context: '--- index.html ---\n<h1>Old</h1>',
    history: [{ role: 'user', content: 'Use a quiet layout.' }],
  })

  assert.match(request.context, /Old/)
  assert.equal(request.history[0].content, 'Use a quiet layout.')
})

test('buildProject follows a background job without holding one request open', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const urls = []
  globalThis.fetch = async (url) => {
    urls.push(url)
    if (url === '/api/project') {
      return new Response(JSON.stringify({ status: 'running', jobId: 'a'.repeat(32) }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify({
      status: 'completed',
      files: [{ path: 'app.js', content: 'console.log("done")' }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  const result = await buildProject('Build it', 'token')
  assert.equal(result.files[0].path, 'app.js')
  assert.equal(urls[1], `/api/project?jobId=${'a'.repeat(32)}`)
})

test('text attachments retain a bounded regeneration payload while visual files do not', () => {
  const stored = stripAttachmentPayloads([
    { name: 'notes.md', size: 50_000, kind: 'text', type: 'text/markdown', content: 'x'.repeat(50_000) },
    { name: 'photo.png', size: 100, kind: 'image', type: 'image/png', b64: 'secret' },
  ])

  assert.equal(stored[0].content.length, 32_000)
  assert.equal(stored[1].b64, undefined)
})

test('sampled video contact sheets are routed to vision as an image with context', () => {
  const packed = packAttachments([
    { name: 'demo.mp4', kind: 'video', frameB64: 'frames' },
  ])

  assert.equal(packed.imageB64, 'frames')
  assert.match(packed.textContext, /sampled frames from video: demo\.mp4/i)
  assert.deepEqual(packed.unsupported, [])
})

test('respondGeneratedImage uses the authenticated backend image payload', async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let authorization
  globalThis.fetch = async (_url, options) => {
    authorization = options.headers.Authorization
    return new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: {
        'Content-Type': 'image/jpeg',
        'X-Image-Source': 'https://image.example/generated.jpg',
      },
    })
  }

  const result = await respondGeneratedImage('draw an orbiting city', 'token')
  assert.equal(authorization, 'Bearer token')
  assert.match(result.image.url, /^blob:/)
  assert.equal(persistableImage(result.image).url, 'https://image.example/generated.jpg')
  assert.match(result.provenance, /image generator/i)
})
