import { useEffect, useRef, useState } from 'react'
import { useAuth } from './auth.jsx'
import {
  loadProjects,
  saveProjects,
  newProjectChat,
  looksBinary,
  fileBytes,
  FILE_LIMITS,
  mergeProjectFiles,
} from './projectStore.js'
import { newId, titleFrom } from './store.js'
import {
  dispatchEngineReply,
  continueOmni,
  continueEdge,
  buildProject,
  downloadProject,
  summarizeMemory,
  readAttachments,
  stripAttachmentPayloads,
  persistableImage,
  slugify,
  checkProjects,
  projectFilesContext,
  DEFAULT_MODE,
} from './engine.js'
import { useEngineProbe } from './useEngineProbe.js'
import { applyAppearance, composeSystemPrompt, loadSettings } from './settings.js'
import Sidebar from './Sidebar.jsx'
import SettingsModal from './SettingsModal.jsx'
import RegistryPanel from './RegistryPanel.jsx'
import ListboxSelect from './ListboxSelect.jsx'
import Composer from './Composer.jsx'
import Message from './Message.jsx'
import PendingActionInbox from './capabilities/PendingActionInbox.jsx'
import CapabilityScopeControls from './capabilities/CapabilityScopeControls.jsx'
import CapabilitySelect from './capabilities/CapabilitySelect.jsx'

/*
 * A project's workspace. Two views, one component:
 *   #/projects/<id>          -> project home: composer + Recents list
 *   #/projects/<id>/c/<cid>  -> one chat inside the project
 *
 * Deliberately ONE component rather than two routed ones. Sending from the
 * home view creates a chat and navigates into it while the reply is still
 * streaming; if the two views were separate components React would unmount
 * the first mid-stream and the in-flight reply would be lost. Same component
 * type at the same tree position means React reconciles rather than
 * remounts, so the stream survives the navigation.
 *
 * Memory is project-level (what the PROJECT established, shared across all
 * its chats) while memorySyncedCount is per-chat, so each chat only pays to
 * summarize turns that haven't been folded in yet.
 */

const TEAMS = [
  { value: 'auto', label: 'Auto route' },
  { value: 'code', label: 'Team · Code' },
  { value: 'brain', label: 'Team · Brain' },
  { value: 'vision', label: 'Team · Vision' },
  { value: 'design', label: 'Team · Design' },
]

const MODES = [
  { value: 'fast', label: 'Fast' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'deep', label: 'Deep' },
]

function fmtBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function fmtRelative(ts) {
  const s = (Date.now() - ts) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? '1 day ago' : `${d} days ago`
}

const MEMORY_EVERY = 6

export default function ProjectWorkspace({ projectId, chatId }) {
  const { user, getToken } = useAuth()
  const probe = useEngineProbe()
  const { omni, edge } = probe

  const [project, setProject] = useState(
    () => loadProjects(user.id).find((p) => p.id === projectId) || null,
  )
  const [streaming, setStreaming] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [projectDetailsOpen, setProjectDetailsOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [registryOpen, setRegistryOpen] = useState(false)
  const [dragging, setDragging] = useState(false)

  const [team, setTeam] = useState('auto')
  const [mode, setMode] = useState(DEFAULT_MODE)
  const [capabilityId, setCapabilityId] = useState('')
  const [instructionsDraft, setInstructionsDraft] = useState(project?.instructions || '')
  const [buildTask, setBuildTask] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState('')
  const [agentUp, setAgentUp] = useState(false)
  const [fileError, setFileError] = useState('')
  const [memoryBusy, setMemoryBusy] = useState(false)

  const timerRef = useRef(null)
  const stopRef = useRef(false)
  const dragDepth = useRef(0)
  const scrollRef = useRef(null)
  const uploadRef = useRef(null)
  const buildAbortRef = useRef(null)

  const activeChat = chatId ? project?.chats.find((c) => c.id === chatId) || null : null

  useEffect(() => {
    applyAppearance(loadSettings(user.id))
  }, [user.id])

  useEffect(() => {
    let alive = true
    const probeAgent = () => checkProjects().then((ok) => alive && setAgentUp(ok))
    probeAgent()
    const id = setInterval(probeAgent, 20000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [activeChat?.messages, streaming])

  useEffect(() => () => {
    clearInterval(timerRef.current)
    buildAbortRef.current?.abort()
  }, [])

  /* Persist. Storage failures are surfaced, not swallowed: projects hold
     uploaded file bodies, so filling the ~5MB origin budget is realistic,
     and a file that silently failed to save while still showing in the list
     is worse than an honest error. */
  const persist = (next, projects = null) => {
    setProject(next)
    const all = (projects || loadProjects(user.id)).map((p) => (p.id === next.id ? next : p))
    const res = saveProjects(user.id, all)
    if (!res.ok) {
      setFileError(
        res.quota
          ? 'Browser storage is full — this change was not saved. Remove some files or delete an old project, then try again.'
          : 'Could not write to browser storage — this change was not saved.',
      )
      return false
    }
    return true
  }

  const patchChat = (proj, cid, fn) => ({
    ...proj,
    updatedAt: Date.now(),
    chats: proj.chats.map((c) => (c.id === cid ? fn(c) : c)),
  })

  const streamReply = (reply, cid) => {
    setStreaming(true)
    let i = 0
    const full = reply.text
    timerRef.current = setInterval(() => {
      i += 4 + Math.floor(Math.random() * 5)
      const done = i >= full.length
      const slice = done ? full : full.slice(0, i)
      setProject((prev) => {
        if (!prev) return prev
        const next = patchChat(prev, cid, (c) => ({
          ...c,
          updatedAt: Date.now(),
          messages: c.messages.map((m, idx) =>
            idx === c.messages.length - 1
              ? {
                  ...m,
                  content: slice,
                  project: done ? reply.project : null,
                  image: reply.image || null,
                  sources: done ? reply.sources || null : null,
                  truncated: done ? !!reply.truncated : false,
                  provenance: reply.provenance || 'VibeAI assistant',
                  capabilitySnapshot: done ? reply.capabilitySnapshot || null : null,
                }
              : m,
          ),
        }))
        if (done) {
          const stored = patchChat(next, cid, (c) => ({
            ...c,
            messages: c.messages.map((message, index) =>
              index === c.messages.length - 1
                ? { ...message, image: persistableImage(message.image) }
                : message,
            ),
          }))
          saveProjects(user.id, loadProjects(user.id).map((p) => (p.id === stored.id ? stored : p)))
        }
        return next
      })
      if (done) {
        clearInterval(timerRef.current)
        setStreaming(false)
        setTimeout(() => refreshMemory(cid), 0)
      }
    }, 35)
  }

  const appendContinuation = (cid, messageId, baseText, addition, stillTruncated) => {
    setStreaming(true)
    let i = 0
    timerRef.current = setInterval(() => {
      i += 4 + Math.floor(Math.random() * 5)
      const done = i >= addition.length
      const slice = done ? addition : addition.slice(0, i)
      setProject((prev) => {
        if (!prev) return prev
        const next = patchChat(prev, cid, (c) => ({
          ...c,
          messages: c.messages.map((m) =>
            m.id === messageId
              ? { ...m, content: baseText + slice, truncated: done ? stillTruncated : false }
              : m,
          ),
        }))
        if (done) {
          saveProjects(user.id, loadProjects(user.id).map((p) => (p.id === next.id ? next : p)))
        }
        return next
      })
      if (done) {
        clearInterval(timerRef.current)
        setStreaming(false)
      }
    }, 35)
  }

  /* Fold new turns into project memory. Threshold-gated (each pass is its
     own model call), reads the freshest record off storage rather than
     closing over `project` (this fires from a timeout after streaming, so a
     captured value is stale), and fails silently -- memory is an
     enhancement, and an unreachable summarizer should never surface an error
     over a conversation that otherwise worked. */
  const refreshMemory = async (cid) => {
    try {
      const current = loadProjects(user.id).find((p) => p.id === projectId)
      const chat = current?.chats.find((c) => c.id === cid)
      if (!chat) return
      const synced = chat.memorySyncedCount || 0
      const unsynced = chat.messages.slice(synced)
      if (unsynced.length < MEMORY_EVERY) return

      setMemoryBusy(true)
      const token = await getToken()
      const memory = await summarizeMemory({
        existingMemory: current.memory || '',
        newMessages: unsynced,
        token,
        probe,
      })
      // Re-read: the user may have sent more while this was in flight.
      const latest = loadProjects(user.id).find((p) => p.id === projectId)
      if (!latest) return
      const next = {
        ...latest,
        memory,
        memoryUpdatedAt: Date.now(),
        chats: latest.chats.map((c) =>
          c.id === cid ? { ...c, memorySyncedCount: chat.messages.length } : c,
        ),
      }
      setProject((p) => (p && p.id === projectId ? next : p))
      saveProjects(user.id, loadProjects(user.id).map((p) => (p.id === projectId ? next : p)))
    } catch {
      /* leave memory as it was */
    } finally {
      setMemoryBusy(false)
    }
  }

  const dispatchReply = async (proj, cid, text, sent) => {
    stopRef.current = false
    const chat = proj.chats.find((c) => c.id === cid)
    const history = (chat?.messages || []).slice(0, -2)
    const filesContext = projectFilesContext(proj.files)
    setStreaming(true)
    const reply = await dispatchEngineReply({
      text,
      history,
      convId: cid,
      sent,
      team,
      mode,
      systemPrompt: [
        composeSystemPrompt(loadSettings(user.id), proj.instructions, proj.memory),
        filesContext
          ? `Project files (authoritative current content):\n${filesContext}`
          : '',
      ].filter(Boolean).join('\n\n'),
      getToken,
      probe,
      projectId: proj.id,
      chatId: cid,
      capabilityIds: capabilityId ? [capabilityId] : [],
    })
    if (stopRef.current) return
    streamReply(reply, cid)
  }

  const handleSend = (text) => {
    if (streaming || !project) return
    const sent = attachments
    const turn = [
      {
        id: newId(),
        role: 'user',
        content: text,
        // Keep capped text for faithful regeneration, but never persist
        // visual/base64 payloads -- see stripAttachmentPayloads.
        attachments: stripAttachmentPayloads(sent),
        ts: Date.now(),
      },
      { id: newId(), role: 'assistant', content: '', ts: Date.now() },
    ]

    let next
    let cid
    if (activeChat) {
      cid = activeChat.id
      next = patchChat(project, cid, (c) => ({
        ...c,
        title: c.messages.length === 0 ? titleFrom(text) : c.title,
        updatedAt: Date.now(),
        messages: [...c.messages, ...turn],
      }))
    } else {
      // Sending from the project home starts a new chat and moves into it.
      const chat = { ...newProjectChat(), title: titleFrom(text), messages: turn }
      cid = chat.id
      next = { ...project, updatedAt: Date.now(), chats: [chat, ...project.chats] }
      window.location.hash = `#/projects/${project.id}/c/${cid}`
    }

    persist(next)
    setAttachments([])
    dispatchReply(next, cid, text, sent)
  }

  const handleStop = () => {
    stopRef.current = true
    clearInterval(timerRef.current)
    setStreaming(false)
    setProject((prev) => {
      if (prev) saveProjects(user.id, loadProjects(user.id).map((p) => (p.id === prev.id ? prev : p)))
      return prev
    })
  }

  const handleRegenerate = () => {
    if (!project || !activeChat || streaming) return
    const lastUser = [...activeChat.messages].reverse().find((m) => m.role === 'user')
    if (!lastUser) return
    const unavailable = (lastUser.attachments || []).some(
      (attachment) => attachment.kind !== 'text' || (!attachment.content && !attachment.error),
    )
    if (unavailable) {
      window.alert('Please reattach the image or video before regenerating this answer.')
      return
    }
    const next = patchChat(project, activeChat.id, (c) => ({
      ...c,
      messages: [
        ...c.messages.filter((_, i) => i !== c.messages.length - 1),
        { id: newId(), role: 'assistant', content: '', ts: Date.now() },
      ],
    }))
    persist(next)
    dispatchReply(next, activeChat.id, lastUser.content, lastUser.attachments || [])
  }

  const handleContinue = async (messageId) => {
    if (!project || !activeChat || streaming) return
    const idx = activeChat.messages.findIndex((m) => m.id === messageId)
    if (idx === -1) return
    const msg = activeChat.messages[idx]
    const history = activeChat.messages.slice(0, idx)
    const cid = activeChat.id

    setProject((prev) =>
      patchChat(prev, cid, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === messageId ? { ...m, truncated: false } : m)),
      })),
    )
    setStreaming(true)
    try {
      let result
      if (omni) {
        result = await continueOmni(history, msg.content, mode)
      } else if (edge) {
        const token = await getToken()
        result = await continueEdge(history, msg.content, token, mode)
      } else {
        setStreaming(false)
        return
      }
      appendContinuation(cid, messageId, msg.content, result.text, result.truncated)
    } catch {
      setStreaming(false)
      setProject((prev) =>
        patchChat(prev, cid, (c) => ({
          ...c,
          messages: c.messages.map((m) => (m.id === messageId ? { ...m, truncated: true } : m)),
        })),
      )
    }
  }

  const commitInstructions = () => {
    if (!project || instructionsDraft === project.instructions) return
    persist({ ...project, instructions: instructionsDraft, updatedAt: Date.now() })
  }

  const clearMemory = () => {
    if (!project) return
    persist({
      ...project,
      memory: '',
      memoryUpdatedAt: null,
      chats: project.chats.map((c) => ({ ...c, memorySyncedCount: c.messages.length })),
    })
  }

  const runBuild = async () => {
    const trimmed = buildTask.trim()
    if (!trimmed || building || !project) return
    setBuilding(true)
    setBuildError('')
    try {
      const token = await getToken()
      const controller = new AbortController()
      buildAbortRef.current = controller
      const result = await buildProject(trimmed, token, 'coding', project.id, {
        context: projectFilesContext(project.files, 48_000),
        history: activeChat?.messages || [],
        chatId: activeChat?.id || null,
        capabilityIds: capabilityId ? [capabilityId] : [],
        signal: controller.signal,
      })
      const projects = loadProjects(user.id)
      const latest = projects.find((p) => p.id === project.id) || project
      const merged = mergeProjectFiles(
        latest.files,
        result.files.map((file) => ({ ...file, source: 'agent' })),
      )
      const latestBuild = {
        summary: result.summary,
        iterations: result.iterations,
        totalMs: result.totalMs,
        filesTruncated: result.filesTruncated,
      }
      persist({
        ...latest,
        files: merged,
        latestBuild,
        updatedAt: Date.now(),
      }, projects)
      setBuildTask('')
    } catch (err) {
      setBuildError(String(err?.message || err))
    } finally {
      buildAbortRef.current = null
      setBuilding(false)
    }
  }

  /* Upload reference files INTO the project (distinct from Composer's
     attachment chips, which ride along with one message and carry only
     name+size). Read for real with FileReader so they can be shown, zipped,
     and fed to the agent. Rejects binaries and oversized files rather than
     storing them: the whole list is JSON-serialised into localStorage, where
     an image would both bloat the bucket and survive only as mojibake. */
  const uploadFiles = async (fileList) => {
    if (!project) return
    setFileError('')
    const accepted = []
    const rejected = []

    for (const file of Array.from(fileList)) {
      if (file.size > FILE_LIMITS.perFileBytes) {
        rejected.push(`${file.name} (over ${Math.round(FILE_LIMITS.perFileBytes / 1024)} KB)`)
        continue
      }
      let text
      try {
        text = await file.text()
      } catch {
        rejected.push(`${file.name} (unreadable)`)
        continue
      }
      if (looksBinary(text)) {
        rejected.push(`${file.name} (binary)`)
        continue
      }
      accepted.push({ path: file.webkitRelativePath || file.name, content: text, source: 'upload' })
    }

    if (accepted.length) {
      const merged = mergeProjectFiles(project.files, accepted)
      if (merged.length > FILE_LIMITS.maxFiles) {
        setFileError(`A project holds at most ${FILE_LIMITS.maxFiles} files.`)
        return
      }
      if (fileBytes(merged) > FILE_LIMITS.perProjectBytes) {
        setFileError(
          `That would exceed this project's ${Math.round(
            FILE_LIMITS.perProjectBytes / 1024 / 1024,
          )} MB file budget.`,
        )
        return
      }
      persist({ ...project, files: merged, updatedAt: Date.now() })
    }

    if (rejected.length) setFileError(`Skipped ${rejected.join(', ')}.`)
  }

  const removeFile = (path) =>
    project &&
    persist({ ...project, files: project.files.filter((f) => f.path !== path), updatedAt: Date.now() })

  const deleteChat = (cid) => {
    if (!project) return
    if (!window.confirm('Delete this chat?')) return
    persist({ ...project, chats: project.chats.filter((c) => c.id !== cid), updatedAt: Date.now() })
    if (chatId === cid) window.location.hash = `#/projects/${project.id}`
  }

  /* Same real-bytes read as ChatApp: metadata alone never reached a model,
     so an attached image or document produced an answer about nothing. */
  const addFiles = async (fileList) => {
    try {
      const room = Math.max(0, 20 - attachments.length)
      if (!room) return
      const read = await readAttachments(Array.from(fileList).slice(0, room))
      setAttachments((a) => [...a, ...read].slice(0, 20))
    } catch (err) {
      console.error('[project] could not read attachments:', err)
    }
  }

  const onDragEnter = (e) => {
    e.preventDefault()
    dragDepth.current += 1
    setDragging(true)
  }
  const onDragLeave = (e) => {
    e.preventDefault()
    dragDepth.current -= 1
    if (dragDepth.current <= 0) {
      dragDepth.current = 0
      setDragging(false)
    }
  }
  const onDrop = (e) => {
    e.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files)
  }

  const goToChat = () => {
    window.location.hash = '#/chat'
  }

  const shell = (children) => (
    <div
      className="chat-app"
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-box">⬆ Drop files to attach</div>
        </div>
      )}
      <Sidebar
        chats={[]}
        activeId={null}
        onSelect={goToChat}
        onNew={goToChat}
        onRename={() => {}}
        onDelete={() => {}}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenRegistry={() => setRegistryOpen(true)}
      />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <RegistryPanel open={registryOpen} onClose={() => setRegistryOpen(false)} />
      {children}
    </div>
  )

  if (!project) {
    return shell(
      <main className="chat-main">
        <div className="proj-page proj-not-found">
          <h1>Project not found</h1>
          <p>It may have been deleted, or the link is wrong.</p>
          <a className="proj-build" href="#/projects">
            Back to Projects
          </a>
        </div>
      </main>,
    )
  }

  const totalBytes = fileBytes(project.files)

  const composerToolbar = (
    <>
      <ListboxSelect
        className="proj-model-pill"
        options={TEAMS}
        value={team}
        aria-label="Route to team"
        onChange={setTeam}
      />
      <CapabilitySelect value={capabilityId} onChange={setCapabilityId} />
      <ListboxSelect
        className="proj-model-pill"
        options={MODES}
        value={mode}
        aria-label="Reasoning depth"
        onChange={setMode}
      />
    </>
  )

  return shell(
    <main className="chat-main proj-workspace">
      <header className="chat-top">
        <button
          className="hamburger"
          aria-label="Toggle chat history"
          onClick={() => setSidebarOpen((o) => !o)}
        >
          ☰
        </button>
        <a className="proj-back" href="#/projects">
          ← All projects
        </a>
        <div className="chat-top-right">
          <PendingActionInbox />
          <button
            type="button"
            className="proj-details-toggle"
            aria-label="Toggle project details"
            aria-controls="project-details"
            aria-expanded={projectDetailsOpen}
            onClick={() => setProjectDetailsOpen((open) => !open)}
          >
            Details
          </button>
          <button
            type="button"
            className={`proj-star${project.starred ? ' is-on' : ''}`}
            aria-label={project.starred ? 'Unstar project' : 'Star project'}
            aria-pressed={!!project.starred}
            onClick={() => persist({ ...project, starred: !project.starred })}
          >
            {project.starred ? '★' : '☆'}
          </button>
        </div>
      </header>

      <div className="proj-workspace-body">
        <div className="proj-chat-col">
          <div className="chat-scroll" ref={scrollRef}>
            {activeChat ? (
              <div className="msg-list">
                {activeChat.messages.map((m, i) => (
                  <Message
                    key={m.id}
                    msg={m}
                    entryNo={i + 1}
                    isStreaming={
                      streaming && i === activeChat.messages.length - 1 && m.role === 'assistant'
                    }
                    canRegenerate={i === activeChat.messages.length - 1}
                    onRegenerate={handleRegenerate}
                    onContinue={handleContinue}
                  />
                ))}
              </div>
            ) : (
              <div className="proj-home">
                <input
                  className="proj-title-input"
                  value={project.name}
                  onChange={(e) => setProject((p) => ({ ...p, name: e.target.value }))}
                  onBlur={(e) =>
                    persist({ ...project, name: e.target.value.trim() || 'Untitled project' })
                  }
                  aria-label="Project name"
                />
                {project.description && <p className="proj-home-desc">{project.description}</p>}

                <p className="proj-side-h3 proj-recents-label">Recents</p>
                {project.chats.length === 0 ? (
                  <p className="proj-side-hint">
                    No chats yet — start one below. Everything you discuss here shares this
                    project's instructions, memory and files.
                  </p>
                ) : (
                  <ul className="proj-recents">
                    {[...project.chats]
                      .sort((a, b) => b.updatedAt - a.updatedAt)
                      .map((c) => (
                        <li key={c.id}>
                          <a className="proj-recent" href={`#/projects/${project.id}/c/${c.id}`}>
                            <svg viewBox="0 0 16 16" aria-hidden="true" className="proj-recent-icon">
                              <path
                                d="M2 4.5A1.5 1.5 0 0 1 3.5 3h9A1.5 1.5 0 0 1 14 4.5v5A1.5 1.5 0 0 1 12.5 11H6l-3 2.5V11h-.5A1.5 1.5 0 0 1 1 9.5v-5"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="1.3"
                                strokeLinejoin="round"
                              />
                            </svg>
                            <span className="proj-recent-title">{c.title}</span>
                            <span className="proj-recent-time">{fmtRelative(c.updatedAt)}</span>
                          </a>
                          <button
                            type="button"
                            className="proj-file-remove"
                            aria-label={`Delete ${c.title}`}
                            onClick={() => deleteChat(c.id)}
                          >
                            ×
                          </button>
                        </li>
                      ))}
                  </ul>
                )}
              </div>
            )}
          </div>
          <Composer
            onSend={handleSend}
            streaming={streaming}
            onStop={handleStop}
            attachments={attachments}
            onAddFiles={addFiles}
            onRemoveAttachment={(i) => setAttachments((list) => list.filter((_, j) => j !== i))}
            toolbar={composerToolbar}
            placeholder={activeChat ? 'Reply in this chat…' : 'Start a new chat in this project…'}
            hint="Enter to send · Shift+Enter for a new line · this chat shares the project's instructions, memory and files"
          />
        </div>

        {projectDetailsOpen && (
          <button
            type="button"
            className="proj-side-scrim"
            aria-label="Close project details"
            onClick={() => setProjectDetailsOpen(false)}
          />
        )}
        <aside id="project-details" className={`proj-side${projectDetailsOpen ? ' is-open' : ''}`}>
          <CapabilityScopeControls projectId={project.id} chatId={activeChat?.id} />
          <section className="proj-side-section">
            <div className="proj-side-head">
              <h3 className="proj-side-h3">Memory</h3>
              {project.memory && (
                <button type="button" className="proj-download-link" onClick={clearMemory}>
                  Clear
                </button>
              )}
            </div>
            {memoryBusy && <p className="proj-side-hint">Updating…</p>}
            {project.memory ? (
              <p className="proj-memory">{project.memory}</p>
            ) : (
              !memoryBusy && (
                <p className="proj-side-hint">
                  Project memory will show here after a few chats — a short summary of what this
                  project has established, sent along with every message.
                </p>
              )
            )}
          </section>

          <section className="proj-side-section">
            <h3 className="proj-side-h3">Instructions</h3>
            <p className="proj-side-hint">
              Applied to every chat in this project, on top of your global personalization.
            </p>
            <textarea
              className="set-input proj-instructions"
              rows={4}
              value={instructionsDraft}
              onChange={(e) => setInstructionsDraft(e.target.value)}
              onBlur={commitInstructions}
              placeholder="e.g. this is a FastAPI backend, prefer async, keep responses terse"
            />
          </section>

          <section className="proj-side-section">
            <h3 className="proj-side-h3">Build</h3>
            <p className="proj-side-hint">
              {agentUp
                ? 'The real agent writes files here, with a build/test gate.'
                : 'Agent backend not reachable right now.'}
            </p>
            <textarea
              className="set-input proj-textarea"
              rows={2}
              value={buildTask}
              disabled={building || !agentUp}
              onChange={(e) => setBuildTask(e.target.value)}
              placeholder="Describe what to build or change"
            />
            <button
              type="button"
              className="proj-build"
              onClick={runBuild}
              disabled={building || !buildTask.trim() || !agentUp}
            >
              {building ? 'Building…' : 'Build'}
            </button>
            {buildError && (
              <p className="proj-error" role="alert">
                {buildError}
              </p>
            )}
            {project.latestBuild && !buildError && (
              <div className="proj-build-result" aria-live="polite">
                <strong>Latest build completed</strong>
                <span>
                  {project.latestBuild.iterations} iterations · {(project.latestBuild.totalMs / 1000).toFixed(1)}s
                </span>
                {project.latestBuild.summary && <p>{project.latestBuild.summary}</p>}
                {project.latestBuild.filesTruncated && (
                  <p>Some generated files were too large to copy into the browser.</p>
                )}
              </div>
            )}
          </section>

          <section className="proj-side-section">
            <div className="proj-side-head">
              <h3 className="proj-side-h3">Files</h3>
              {project.files.length > 0 && (
                <button
                  type="button"
                  className="proj-download-link"
                  onClick={() =>
                    downloadProject({ slug: slugify(project.name), files: project.files })
                  }
                >
                  Download all
                </button>
              )}
            </div>

            {project.files.length === 0 ? (
              <button
                type="button"
                className="proj-dropzone"
                onClick={() => uploadRef.current?.click()}
              >
                <svg viewBox="0 0 64 40" aria-hidden="true" className="proj-dropzone-art">
                  <rect x="2" y="9" width="20" height="26" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                  <rect x="16" y="4" width="20" height="31" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                  <rect x="38" y="7" width="22" height="28" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="3 3" />
                  <path d="M49 17v8M45 21h8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
                <span>Add documents or other text to reference in this project.</span>
              </button>
            ) : (
              <>
                <ul className="proj-files">
                  {project.files.map((f) => (
                    <li key={f.path}>
                      <code className="proj-file-path">{f.path}</code>
                      <span className="proj-file-row-right">
                        {f.source === 'upload' && <span className="proj-file-tag">added</span>}
                        <span className="proj-file-size">{fmtBytes(new Blob([f.content]).size)}</span>
                        <button
                          type="button"
                          className="proj-file-remove"
                          aria-label={`Remove ${f.path}`}
                          onClick={() => removeFile(f.path)}
                        >
                          ×
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="proj-side-hint">
                  {fmtBytes(totalBytes)} of {fmtBytes(FILE_LIMITS.perProjectBytes)} used
                </p>
                <button
                  type="button"
                  className="proj-add-file"
                  onClick={() => uploadRef.current?.click()}
                >
                  + Add files
                </button>
              </>
            )}

            <input
              ref={uploadRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                uploadFiles(e.target.files)
                e.target.value = ''
              }}
            />
            {fileError && (
              <p className="proj-error" role="alert">
                {fileError}
              </p>
            )}
          </section>
        </aside>
      </div>
    </main>,
  )
}
