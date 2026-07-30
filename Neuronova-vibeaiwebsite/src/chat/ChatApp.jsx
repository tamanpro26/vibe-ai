import { useEffect, useRef, useState } from 'react'
import { useAuth } from './auth.jsx'
import { loadChats, saveChats, newConversation, newId, titleFrom } from './store.js'
import {
  respond,
  respondLive,
  checkLive,
  respondTeam,
  checkTeam,
  respondOmni,
  checkOmni,
  respondEdge,
  checkEdge,
  continueOmni,
  continueEdge,
  DEFAULT_MODE,
} from './engine.js'
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

const MODES = [
  { value: 'fast', label: 'Fast', title: 'Quick answers, smaller model. Best for simple asks.' },
  { value: 'balanced', label: 'Balanced', title: 'Default: solid quality without added latency.' },
  { value: 'deep', label: 'Deep', title: 'Slower, reasons through the problem first. Best for hard questions.' },
]

const SUGGESTIONS = [
  { icon: '⌨', text: 'Build a Python CLI that renames files in bulk' },
  { icon: '🔍', text: 'Research the best free-tier LLM providers right now' },
  { icon: '✍', text: 'Write a LinkedIn post about a solo-built AI project' },
  { icon: '🧠', text: 'Explain how a 5-stage reasoning council beats one model' },
]

export default function ChatApp() {
  const { user, getToken } = useAuth()
  const [chats, setChats] = useState(() => loadChats(user.id))
  const [activeId, setActiveId] = useState(() => loadChats(user.id)[0]?.id ?? null)
  const [team, setTeam] = useState('auto')
  const [mode, setMode] = useState(DEFAULT_MODE)
  const [streaming, setStreaming] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [dragging, setDragging] = useState(false)
  const [live, setLive] = useState(false)
  // Named distinctly from `team` (the routing selector above) -- this is
  // whether the real Manager/Council is reachable via api/team.js, not
  // which team a task routes to.
  const [manager, setManager] = useState(false)
  const [omni, setOmni] = useState(false)
  const [edge, setEdge] = useState(false)
  const timerRef = useRef(null)
  const stopRef = useRef(false)
  const dragDepth = useRef(0)
  const scrollRef = useRef(null)
  // Mirrors live/manager/omni/edge state, updated synchronously (no waiting
  // on a React re-render) so dispatchReply can always read the freshest
  // known values -- see probeRef below for why this exists.
  const engineRef = useRef({ live: false, manager: false, omni: false, edge: false })
  // Holds the currently in-flight probe's promise. A message sent in the
  // first couple seconds after page load can race ahead of the very first
  // health-check probe (it's a real network round trip, not instant) --
  // without this, dispatchReply would see the initial `false` defaults and
  // fall straight to the simulated engine even though everything actually
  // works, just a moment too early to know it yet. Reproduced live: sending
  // "wassup" immediately on page load hit "[CB] no live engine reachable"
  // despite every tier being confirmed working seconds later.
  const probeRef = useRef(null)

  // Probe all four engines; re-check so starting a local server (or the
  // edge function / team proxy going live on deploy) upgrades the chat
  // without a reload. Probed in parallel: a down server costs a full
  // timeout, and serially that would quadruple the delay.
  useEffect(() => {
    let alive = true
    const probe = async () => {
      const [okLive, okManager, okOmni, okEdge] = await Promise.all([
        checkLive(),
        checkTeam(),
        checkOmni(),
        checkEdge(),
      ])
      engineRef.current = { live: okLive, manager: okManager, omni: okOmni, edge: okEdge }
      if (!alive) return
      setLive(okLive)
      setManager(okManager)
      setOmni(okOmni)
      setEdge(okEdge)
    }
    probeRef.current = probe()
    const id = setInterval(() => {
      probeRef.current = probe()
    }, 20000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

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
                    ? {
                        ...m,
                        content: slice,
                        project: done ? reply.project : null,
                        // Attach the image job as soon as streaming STARTS, not
                        // on completion: the render bay should open and begin
                        // scanning while the text types, so the ~17s fetch is
                        // already underway by the time the copy finishes.
                        image: reply.image || null,
                        // Sources land only when the answer completes: showing
                        // citations beside half-typed prose reads as if the
                        // claims are already sourced when they aren't yet.
                        sources: done ? reply.sources || null : null,
                        // Surfaced only once the full (possibly cut-off) text
                        // has actually finished typing out, so the Continue
                        // affordance can't appear mid-type and get confused
                        // for part of the answer itself.
                        truncated: done ? !!reply.truncated : false,
                      }
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

  // Types out a continuation ONTO existing message content, rather than
  // replacing it from empty like streamReply -- used after a truncated
  // reply's Continue action returns more text.
  const appendContinuation = (convId, messageId, baseText, addition, stillTruncated) => {
    setStreaming(true)
    let i = 0
    timerRef.current = setInterval(() => {
      i += 4 + Math.floor(Math.random() * 5)
      const done = i >= addition.length
      const slice = done ? addition : addition.slice(0, i)
      setChats((prev) => {
        const next = prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === messageId
                    ? { ...m, content: baseText + slice, truncated: done ? stillTruncated : false }
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

  // Engine cascade, best-answer first:
  //   1. VibeAI API  - the real Manager, direct (localhost:8000, dev only)
  //   2. Team proxy  - the SAME real Manager, reached via our own
  //                    /api/team on the public deploy (Render-hosted)
  //   3. OmniRoute   - single-model fallback via the local gateway
  //                    (localhost:20128)
  //   4. Edge proxy  - single-model fallback via our own /api/chat (works
  //                    anywhere, including the public deploy)
  //   5. simulated   - canned templates, last resort
  // Each failure falls through to the next, the same way the model registry's
  // circuit breaker degrades across providers rather than erroring out.
  const dispatchReply = async (convId, text, sent) => {
    stopRef.current = false
    const conv = chats.find((c) => c.id === convId)
    // Drop the just-appended user turn and the empty assistant placeholder;
    // the prompt is passed separately.
    const history = (conv?.messages || []).slice(0, -2)

    setStreaming(true)

    // Wait for any in-flight probe before trusting engineRef -- otherwise a
    // message sent right after page load reads the initial `false`
    // defaults instead of the real (still-resolving) availability.
    if (probeRef.current) await probeRef.current
    const engines = engineRef.current

    if (engines.live) {
      try {
        const reply = await respondLive(text, convId)
        if (stopRef.current) return
        streamReply(convId, reply)
        return
      } catch (err) {
        console.error('[engine:live] failed, falling through:', err)
        setLive(false)                 // fall through to the team proxy
        engineRef.current.live = false
      }
    }

    if (engines.manager) {
      try {
        const token = await getToken()
        const reply = await respondTeam(text, convId, token)
        if (stopRef.current) return
        streamReply(convId, reply)
        return
      } catch (err) {
        console.error('[engine:manager] failed, falling through:', err)
        setManager(false)              // fall through to OmniRoute
        engineRef.current.manager = false
      }
    }

    if (engines.omni) {
      try {
        const reply = await respondOmni(text, history, mode)
        if (stopRef.current) return
        streamReply(convId, reply)
        return
      } catch (err) {
        console.error('[engine:omni] failed, falling through:', err)
        setOmni(false)                 // fall through to the edge proxy
        engineRef.current.omni = false
      }
    }

    if (engines.edge) {
      try {
        const token = await getToken()
        const reply = await respondEdge(text, history, token, mode)
        if (stopRef.current) return
        streamReply(convId, reply)
        return
      } catch (err) {
        console.error('[engine:edge] failed, falling through:', err)
        setEdge(false)                 // fall through to simulated
        engineRef.current.edge = false
      }
    }

    if (stopRef.current) return
    const sim = respond(text, sent, team)
    sim.text = `[CB] no live engine reachable - simulated response\n${sim.text}`
    streamReply(convId, sim)
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
    dispatchReply(convId, text, sent)
  }

  const handleStop = () => {
    stopRef.current = true
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
    dispatchReply(active.id, lastUser.content, lastUser.attachments || [])
  }

  // Recovers from a real, reproduced bug: a reply that hit its token ceiling
  // mid-answer used to just... stop, with the cut text presented as if it
  // were the complete answer. This re-asks for a seamless continuation and
  // appends it, rather than the user having to notice, re-ask, and hope the
  // model resumes where it left off.
  const handleContinue = async (messageId) => {
    if (!active || streaming) return
    const idx = active.messages.findIndex((m) => m.id === messageId)
    if (idx === -1) return
    const msg = active.messages[idx]
    const history = active.messages.slice(0, idx)
    const convId = active.id

    // Hide the affordance immediately so a slow connection can't invite a
    // second click while the first continuation is still in flight.
    setChats((prev) =>
      prev.map((c) =>
        c.id === convId
          ? { ...c, messages: c.messages.map((m) => (m.id === messageId ? { ...m, truncated: false } : m)) }
          : c,
      ),
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
      appendContinuation(convId, messageId, msg.content, result.text, result.truncated)
    } catch {
      setStreaming(false)
      // Restore the affordance so the user can retry rather than silently
      // losing the option after one failed attempt.
      setChats((prev) =>
        prev.map((c) =>
          c.id === convId
            ? { ...c, messages: c.messages.map((m) => (m.id === messageId ? { ...m, truncated: true } : m)) }
            : c,
        ),
      )
    }
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
            <div className="mode-switch" role="radiogroup" aria-label="Reasoning mode">
              {MODES.map((mo) => (
                <button
                  key={mo.value}
                  type="button"
                  role="radio"
                  aria-checked={mode === mo.value}
                  title={mo.title}
                  className={`mode-btn${mode === mo.value ? ' is-active' : ''}`}
                  onClick={() => setMode(mo.value)}
                >
                  {mo.label}
                </button>
              ))}
            </div>
            <span className={`engine-badge${live || manager || omni || edge ? ' is-live' : ''}`}>
              {live
                ? 'LIVE ENGINE'
                : manager
                  ? 'MANAGER TEAM'
                  : omni
                    ? 'OMNIROUTE'
                    : edge
                      ? 'CLOUD ENGINE'
                      : 'SIMULATED ENGINE'}
            </span>
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
                Any task routes to a specialist team — search, research, writing, reasoning.
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
                  onContinue={handleContinue}
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
