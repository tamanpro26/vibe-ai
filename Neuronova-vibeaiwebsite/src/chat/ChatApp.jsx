import { useEffect, useRef, useState } from 'react'
import { useAuth } from './auth.jsx'
import { loadChats, saveChats, newConversation, newId, titleFrom } from './store.js'
import { respond } from './engine.js'
import Sidebar from './Sidebar.jsx'
import Composer from './Composer.jsx'
import Message from './Message.jsx'

const TEAMS = [
  { value: 'auto', label: 'Auto route' },
  { value: 'code', label: 'Team · Code' },
  { value: 'brain', label: 'Team · Brain' },
  { value: 'vision', label: 'Team · Vision' },
  { value: 'design', label: 'Team · Design' },
]

const SUGGESTIONS = [
  { icon: '⌨', text: 'Build a Python CLI that renames files in bulk' },
  { icon: '🌐', text: 'Create a landing page and give me the zip' },
  { icon: '🛡', text: 'Explain the circuit breaker pattern simply' },
  { icon: '🧠', text: 'How does a 5-stage reasoning council beat one model?' },
]

export default function ChatApp() {
  const { user } = useAuth()
  const [chats, setChats] = useState(() => loadChats(user.id))
  const [activeId, setActiveId] = useState(() => loadChats(user.id)[0]?.id ?? null)
  const [team, setTeam] = useState('auto')
  const [streaming, setStreaming] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [dragging, setDragging] = useState(false)
  const timerRef = useRef(null)
  const dragDepth = useRef(0)
  const scrollRef = useRef(null)

  const active = chats.find((c) => c.id === activeId) || null

  const persist = (next) => {
    setChats(next)
    saveChats(user.id, next)
  }

  useEffect(() => () => clearInterval(timerRef.current), [])

  // pin scroll to bottom while messages grow / stream
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chats, streaming])

  const streamReply = (convId, reply) => {
    setStreaming(true)
    let i = 0
    const full = reply.text
    timerRef.current = setInterval(() => {
      i += 4 + Math.floor(Math.random() * 5)
      const done = i >= full.length
      const slice = done ? full : full.slice(0, i)
      setChats((prev) => {
        const next = prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: c.messages.map((m, idx) =>
                  idx === c.messages.length - 1
                    ? { ...m, content: slice, project: done ? reply.project : null }
                    : m,
                ),
              }
            : c,
        )
        if (done) saveChats(user.id, next)
        return next
      })
      if (done) {
        clearInterval(timerRef.current)
        setStreaming(false)
      }
    }, 35)
  }

  const handleSend = (text) => {
    if (streaming) return
    let convId = activeId
    let next = chats
    if (!active) {
      const c = newConversation(team)
      convId = c.id
      next = [c, ...chats]
      setActiveId(c.id)
    }
    const sent = attachments
    next = next.map((c) =>
      c.id === convId
        ? {
            ...c,
            title: c.messages.length === 0 ? titleFrom(text) : c.title,
            team,
            updatedAt: Date.now(),
            messages: [
              ...c.messages,
              { id: newId(), role: 'user', content: text, attachments: sent, ts: Date.now() },
              { id: newId(), role: 'assistant', content: '', ts: Date.now() },
            ],
          }
        : c,
    )
    persist(next)
    setAttachments([])
    streamReply(convId, respond(text, sent, team))
  }

  const handleStop = () => {
    clearInterval(timerRef.current)
    setStreaming(false)
    setChats((prev) => {
      saveChats(user.id, prev)
      return prev
    })
  }

  const handleRegenerate = () => {
    if (!active || streaming) return
    const lastUser = [...active.messages].reverse().find((m) => m.role === 'user')
    if (!lastUser) return
    const next = chats.map((c) =>
      c.id === active.id
        ? {
            ...c,
            updatedAt: Date.now(),
            messages: [
              ...c.messages.filter((_, i) => i !== c.messages.length - 1),
              { id: newId(), role: 'assistant', content: '', ts: Date.now() },
            ],
          }
        : c,
    )
    persist(next)
    streamReply(active.id, respond(lastUser.content, lastUser.attachments || [], team))
  }

  const handleNew = () => {
    if (streaming) handleStop()
    setActiveId(null)
    setSidebarOpen(false)
  }

  const handleSelect = (id) => {
    if (streaming) handleStop()
    setActiveId(id)
    const c = chats.find((x) => x.id === id)
    if (c?.team) setTeam(c.team)
    setSidebarOpen(false)
  }

  const handleRename = (id, title) =>
    persist(chats.map((c) => (c.id === id ? { ...c, title } : c)))

  const handleDelete = (id) => {
    const next = chats.filter((c) => c.id !== id)
    persist(next)
    if (activeId === id) setActiveId(next[0]?.id ?? null)
  }

  const addFiles = (fileList) => {
    const mapped = Array.from(fileList).map((f) => ({
      name: f.webkitRelativePath || f.name,
      size: f.size,
    }))
    setAttachments((a) => [...a, ...mapped].slice(0, 20))
  }

  // drag & drop with depth counter so child enter/leave doesn't flicker
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

  return (
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
        chats={chats}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNew}
        onRename={handleRename}
        onDelete={handleDelete}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <main className="chat-main">
        <header className="chat-top">
          <button
            className="hamburger"
            aria-label="Toggle chat history"
            onClick={() => setSidebarOpen((o) => !o)}
          >
            ☰
          </button>
          <span className="chat-title">{active?.title || 'New chat'}</span>
          <div className="chat-top-right">
            <select
              className="team-select"
              value={team}
              aria-label="Route to team"
              onChange={(e) => setTeam(e.target.value)}
            >
              {TEAMS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <span className="engine-badge">SIMULATED ENGINE</span>
          </div>
        </header>
        <div className="chat-scroll" ref={scrollRef}>
          {!active || active.messages.length === 0 ? (
            <div className="empty-state">
              <svg className="empty-mark" viewBox="0 0 64 64" aria-hidden="true">
                <g stroke="currentColor" strokeWidth="3" fill="none">
                  <path d="M32 8 L53 20 L53 44 L32 56 L11 44 L11 20 Z" />
                  <path d="M32 8 L32 32 M53 20 L32 32 M53 44 L32 32 M32 56 L32 32 M11 44 L32 32 M11 20 L32 32" opacity="0.4" />
                </g>
                <circle cx="32" cy="32" r="5" fill="currentColor" />
              </svg>
              <h1>How can VibeAI help?</h1>
              <p>
                Coding tasks come back as a runnable project with a downloadable zip.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button className="suggestion" key={s.text} onClick={() => handleSend(s.text)}>
                    <span aria-hidden="true">{s.icon}</span>
                    {s.text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="msg-list">
              {active.messages.map((m, i) => (
                <Message
                  key={m.id}
                  msg={m}
                  isStreaming={streaming && i === active.messages.length - 1 && m.role === 'assistant'}
                  canRegenerate={i === active.messages.length - 1}
                  onRegenerate={handleRegenerate}
                />
              ))}
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
        />
      </main>
    </div>
  )
}
