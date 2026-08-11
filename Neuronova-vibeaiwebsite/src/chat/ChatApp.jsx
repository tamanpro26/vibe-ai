import { useEffect, useRef, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { useAuth } from './auth.jsx'
import { loadChats, saveChats, newConversation, newId, titleFrom } from './store.js'
import {
  dispatchEngineReply,
  continueOmni,
  continueEdge,
  readAttachments,
  stripAttachmentPayloads,
  persistableImage,
  DEFAULT_MODE,
} from './engine.js'
import { useEngineProbe } from './useEngineProbe.js'
import {
  startRun,
  cancelRun,
  clearRun,
  runningIds,
  finishedRuns,
  failedRuns,
  subscribeRuns,
} from './runRegistry.js'
import Sidebar from './Sidebar.jsx'
import SettingsModal from './SettingsModal.jsx'
import RegistryPanel from './RegistryPanel.jsx'
import { applyAppearance, composeSystemPrompt, loadSettings } from './settings.js'
import Composer from './Composer.jsx'
import Message from './Message.jsx'
import ListboxSelect from './ListboxSelect.jsx'
import PendingActionInbox from './capabilities/PendingActionInbox.jsx'
import CapabilitySelect from './capabilities/CapabilitySelect.jsx'

const TEAMS = [
  { value: 'auto', label: 'Auto route' },
  { value: 'code', label: 'Team · Code' },
  { value: 'brain', label: 'Team · Brain' },
  { value: 'vision', label: 'Team · Vision' },
  { value: 'design', label: 'Team · Design' },
]

const MODES = [
  { value: 'fast', label: 'Quick', title: 'Uses VibeAI orchestration with a focused, concise final answer.' },
  { value: 'balanced', label: 'Balanced', title: 'Uses VibeAI orchestration with its standard planning, specialist, and review flow.' },
  { value: 'deep', label: 'Deep', title: 'Uses VibeAI orchestration with more deliberate analysis and critique.' },
]

/* One shared entrance for everything that mounts: 8px rise, opacity resolving
   with it. Defined once so the whole app moves with a single grammar rather
   than each surface inventing its own. */
const RISE = {
  hidden: { opacity: 0, y: 8 },
  shown: { opacity: 1, y: 0, transition: { duration: 0.36, ease: [0.22, 0.85, 0.28, 1] } },
}

function welcomeGreeting(name) {
  const hour = new Date().getHours()
  const time = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  return `${time}, ${name}.`
}

export default function ChatApp() {
  const reduceMotion = useReducedMotion()
  const { user, getToken } = useAuth()
  const [chats, setChats] = useState(() => loadChats(user.id))
  const [activeId, setActiveId] = useState(() => loadChats(user.id)[0]?.id ?? null)
  const [team, setTeam] = useState('auto')
  const [mode, setMode] = useState(DEFAULT_MODE)
  const [capabilityId, setCapabilityId] = useState('')
  // Which conversation is mid-typewriter, if any. Distinct from `running`
  // (a request in flight) because the animation is purely local decoration
  // replayed over an already-complete, already-saved string.
  const [typingId, setTypingId] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [registryOpen, setRegistryOpen] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [dragging, setDragging] = useState(false)

  // Composed personalization prompt. Held in a ref, not state, for the same
  // reason useEngineProbe's engineRef exists: dispatchReply is async and
  // would otherwise capture a stale value from the render it started in, so
  // a preference changed mid-conversation would not apply until some later
  // re-render.
  const systemPromptRef = useRef('')

  const refreshSettings = () => {
    if (!user?.id) return
    const s = loadSettings(user.id)
    applyAppearance(s)
    systemPromptRef.current = composeSystemPrompt(s)
  }

  // Re-apply once the user is known. The pre-paint script in index.html
  // already set the theme, but it runs before Clerk resolves who is signed
  // in, so it can only guess at per-user preferences. This corrects them for
  // the actual account -- and matters most when two people share a browser.
  useEffect(() => {
    refreshSettings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id])
  // `live`/`manager`/`omni`/`edge` here are whether each real engine tier is
  // reachable -- distinct from `team` (the routing selector above), which is
  // which team a task routes to.
  const probe = useEngineProbe()
  const { live, manager, omni, edge, ready } = probe
  const timerRef = useRef(null)
  const dragDepth = useRef(0)
  const scrollRef = useRef(null)
  // Ids with a reply in flight. A conversation the user has navigated away
  // from stays in here, which is what lets the sidebar show it still
  // working instead of pretending it stopped.
  const [running, setRunning] = useState([])
  // Read from inside the registry subscription, which is registered once and
  // would otherwise close over a stale activeId forever.
  const activeIdRef = useRef(activeId)
  useEffect(() => {
    activeIdRef.current = activeId
  }, [activeId])

  const active = chats.find((c) => c.id === activeId) || null
  // Streaming means: a run is in flight for THIS conversation, or its reply
  // is currently typing itself out on screen.
  const streaming = activeId !== null && (running.includes(activeId) || typingId === activeId)

  const persist = (next) => {
    setChats(next)
    saveChats(user.id, next)
  }

  useEffect(() => () => clearInterval(timerRef.current), [])

  /* Mirror the run registry into local state.
   *
   * The registry persists finished replies to storage on its own, so this
   * subscription exists only so a MOUNTED ChatApp catches up -- including
   * for a run it never started itself (one begun before the user visited
   * the landing page and came back). Re-reading storage rather than
   * trusting local state is the whole point: local state is exactly what
   * was stale.
   */
  useEffect(() => {
    const sync = () => {
      setChats(loadChats(user.id))
      setRunning(runningIds())
      for (const [id, run] of finishedRuns()) {
        // Already persisted by the registry. Animate only when the user is
        // actually looking at that conversation; otherwise the text is
        // simply there when they return.
        if (id === activeIdRef.current) animateIn(id, run.reply)
        clearRun(id)
      }
      for (const [id, run] of failedRuns()) {
        console.error(`[chat] run ${id} failed:`, run.error)
        const next = persistFailure(id)
        if (id === activeIdRef.current) setChats(next)
        clearRun(id)
      }
    }
    sync() // catch up on mount, then stay subscribed
    return subscribeRuns(sync)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id])

  // pin scroll to bottom while messages grow / stream
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chats, streaming])

  /* Type a finished reply out on screen.
   *
   * Purely cosmetic and purely local: the reply is already complete and
   * already saved by the time this runs, so nothing here can lose it. If
   * the user navigates away mid-animation the text simply appears in full
   * next time -- which is why this no longer writes to storage at all.
   */
  const animateIn = (convId, reply) => {
    clearInterval(timerRef.current)
    setTypingId(convId)
    let i = 0
    const full = reply.text || ''
    timerRef.current = setInterval(() => {
      i += 4 + Math.floor(Math.random() * 5)
      const done = i >= full.length
      const slice = done ? full : full.slice(0, i)
      setChats((prev) =>
        prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: c.messages.map((m, idx) =>
                  idx === c.messages.length - 1
                    ? {
                        ...m,
                        content: slice,
                        project: done ? reply.project : null,
                        // Attach the image job as soon as typing STARTS: the
                        // render bay should open and begin scanning while the
                        // copy types, so the ~17s fetch is already underway
                        // by the time the text finishes.
                        image: reply.image || null,
                        // Sources land only on completion: citations beside
                        // half-typed prose read as if the claims are already
                        // sourced when they aren't yet.
                        sources: done ? reply.sources || null : null,
                        // Same for the Continue affordance -- mid-type it
                        // gets mistaken for part of the answer.
                        truncated: done ? !!reply.truncated : false,
                      }
                    : m,
                ),
              }
            : c,
        ),
      )
      if (done) {
        clearInterval(timerRef.current)
        setTypingId((cur) => (cur === convId ? null : cur))
      }
    }, 35)
  }

  // Types a continuation ONTO existing message content, rather than
  // replacing it from empty like animateIn -- used after a truncated reply's
  // Continue action returns more text. Saves on completion because, unlike
  // animateIn, nothing has persisted this addition yet.
  const appendContinuation = (convId, messageId, baseText, addition, stillTruncated) => {
    clearInterval(timerRef.current)
    setTypingId(convId)
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
        setTypingId((cur) => (cur === convId ? null : cur))
      }
    }, 35)
  }

  /* Write a finished reply into its conversation, reading and writing
     storage directly rather than component state.

     This is what makes a reply survive the user navigating away: it runs
     from the run registry, which outlives this component, so it must not
     depend on `chats` (a stale closure by then) or on ChatApp being mounted
     at all. Returns the updated list so the caller can refresh local state
     when it IS still mounted. */
  const persistReply = (convId, reply) => {
    const next = loadChats(user.id).map((c) =>
      c.id === convId
        ? {
            ...c,
            updatedAt: Date.now(),
            messages: c.messages.map((m, idx) =>
              idx === c.messages.length - 1
                ? {
                    ...m,
                    content: reply.text,
                    project: reply.project || null,
                    image: persistableImage(reply.image),
                    sources: reply.sources || null,
                    truncated: !!reply.truncated,
                    provenance: reply.provenance || 'VibeAI assistant',
                    capabilitySnapshot: reply.capabilitySnapshot || null,
                  }
                : m,
            ),
          }
        : c,
    )
    saveChats(user.id, next)
    return next
  }

  const persistFailure = (convId) => {
    const next = loadChats(user.id).map((conversation) =>
      conversation.id === convId
        ? {
            ...conversation,
            updatedAt: Date.now(),
            messages: conversation.messages.map((message, index) =>
              index === conversation.messages.length - 1 && message.role === 'assistant'
                ? {
                    ...message,
                    content: 'I could not complete that request. Check your connection and try again.',
                    isError: true,
                  }
                : message,
            ),
          }
        : conversation,
    )
    saveChats(user.id, next)
    return next
  }

  // The fallback order itself (live -> manager -> omni -> edge -> simulated)
  // lives in engine.js::dispatchEngineReply, shared with ProjectWorkspace.
  // This wrapper only supplies ChatApp's own concerns: which conversation's
  // history to send, and where the finished answer belongs.
  const dispatchReply = (convId, text, sent, convForHistory) => {
    // Drop the just-appended user turn and the empty assistant placeholder;
    // the prompt is passed separately.
    const history = (convForHistory?.messages || []).slice(0, -2)
    const systemPrompt = systemPromptRef.current

    startRun(convId, {
      dispatch: () =>
        dispatchEngineReply({
          text, history, convId, sent, team, mode, systemPrompt, getToken, probe,
          capabilityIds: capabilityId ? [capabilityId] : [],
        }),
      persist: (reply) => persistReply(convId, reply),
    })
  }

  const handleSend = (text) => {
    if (streaming) return false
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
            ],
          }
        : c,
    )
    try {
      persist(next)
    } catch (error) {
      console.error('[chat] could not save the new message:', error)
      return false
    }
    setAttachments([])
    dispatchReply(convId, text, sent, next.find((c) => c.id === convId))
    return true
  }

  // Explicit user stop only. Navigating away must never call this -- that
  // was the original bug: leaving a conversation discarded a reply that had
  // already come back successfully.
  const handleStop = () => {
    if (activeId) cancelRun(activeId)
    clearInterval(timerRef.current)
    setTypingId(null)
    setRunning(runningIds())
    setChats((prev) => {
      saveChats(user.id, prev)
      return prev
    })
  }

  const handleRegenerate = () => {
    if (!active || streaming) return
    const lastUser = [...active.messages].reverse().find((m) => m.role === 'user')
    if (!lastUser) return
    const unavailable = (lastUser.attachments || []).some(
      (attachment) => attachment.kind !== 'text' || (!attachment.content && !attachment.error),
    )
    if (unavailable) {
      window.alert('Please reattach the image or video before regenerating this answer.')
      return
    }
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
    dispatchReply(
      active.id,
      lastUser.content,
      lastUser.attachments || [],
      next.find((conversation) => conversation.id === active.id),
    )
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
    setTypingId(convId)
    try {
      let result
      if (omni) {
        result = await continueOmni(history, msg.content, mode)
      } else if (edge) {
        const token = await getToken()
        result = await continueEdge(history, msg.content, token, mode)
      } else {
        setTypingId(null)
        return
      }
      appendContinuation(convId, messageId, msg.content, result.text, result.truncated)
    } catch {
      setTypingId(null)
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

  /* Leaving a conversation no longer cancels its reply.
   *
   * Both of these used to call handleStop(), which is what made the AI
   * "stop working" the moment you switched conversations or started a new
   * one: the request kept running, came back fine, and was then thrown
   * away because a stop flag had been set. The run now lives in the
   * registry, finishes on its own, and writes itself to storage -- so
   * switching away and coming back shows the finished answer.
   *
   * Only the in-flight TYPEWRITER is stopped here, since it animates into
   * a conversation that is about to leave the screen. Its text is already
   * saved, so cutting it short loses nothing. */
  const leaveConversation = () => {
    clearInterval(timerRef.current)
    setTypingId(null)
    setSidebarOpen(false)
  }

  const handleNew = () => {
    leaveConversation()
    setActiveId(null)
  }

  const handleSelect = (id) => {
    leaveConversation()
    setActiveId(id)
    const c = chats.find((x) => x.id === id)
    if (c?.team) setTeam(c.team)
  }

  const handleRename = (id, title) =>
    persist(chats.map((c) => (c.id === id ? { ...c, title } : c)))

  const handleDelete = (id) => {
    const next = chats.filter((c) => c.id !== id)
    persist(next)
    if (activeId === id) setActiveId(next[0]?.id ?? null)
  }

  /* Read the files for real.
   *
   * This used to keep only {name, size} -- the bytes were dropped on pickup
   * and no tier ever transmitted anything, so attaching an image or a
   * document and asking about it produced an answer about nothing.
   * readAttachments (engine.js) base64s images for the vision team, reads
   * text files as content, and marks anything it cannot handle so the model
   * is told rather than left to guess. */
  const addFiles = async (fileList) => {
    try {
      const room = Math.max(0, 20 - attachments.length)
      if (!room) return
      const read = await readAttachments(Array.from(fileList).slice(0, room))
      setAttachments((a) => [...a, ...read].slice(0, 20))
    } catch (err) {
      console.error('[chat] could not read attachments:', err)
    }
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

  const displayName = user?.firstName?.trim() || 'there'
  const composerToolbar = (
    <>
      <ListboxSelect
        className="composer-team-select"
        options={TEAMS}
        value={team}
        aria-label="Route to team"
        onChange={setTeam}
      />
      <CapabilitySelect value={capabilityId} onChange={setCapabilityId} />
      <div className="mode-switch composer-mode-switch" role="radiogroup" aria-label="VibeAI reasoning level">
        {MODES.map((reasoningMode) => (
          <button
            key={reasoningMode.value}
            type="button"
            role="radio"
            aria-checked={mode === reasoningMode.value}
            title={reasoningMode.title}
            className={`mode-btn${mode === reasoningMode.value ? ' is-active' : ''}`}
            onClick={() => setMode(reasoningMode.value)}
          >
            {reasoningMode.label}
          </button>
        ))}
      </div>
    </>
  )

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
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenRegistry={() => setRegistryOpen(true)}
      />
      <RegistryPanel open={registryOpen} onClose={() => setRegistryOpen(false)} />
      <SettingsModal
        open={settingsOpen}
        onClose={() => {
          setSettingsOpen(false)
          // Re-read on close so edited instructions apply to the very next
          // message, rather than only after a reload.
          refreshSettings()
        }}
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
            <PendingActionInbox />
            <span className={`engine-badge${live || manager || omni || edge ? ' is-live' : ''}`}>
              {!ready
                ? 'CHECKING ENGINES'
                : live
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
            /* Staggered mount: the headline settles first, then each card
               60ms behind the last. A single simultaneous fade reads as a
               page loading; a stagger reads as an interface arriving.
               `reduceMotion` collapses every offset to zero rather than
               disabling the animation, so the content still appears. */
            <motion.div
              className="empty-state"
              initial="hidden"
              animate="shown"
              variants={{
                hidden: {},
                shown: { transition: { staggerChildren: reduceMotion ? 0 : 0.06 } },
              }}
            >
              <motion.h1 variants={RISE}>{welcomeGreeting(displayName)}</motion.h1>
              <motion.span className="empty-kicker" variants={RISE}>
                VibeAI Council is ready when you are.
              </motion.span>
              <motion.p variants={RISE}>
                Any task routes to a specialist team — search, research, writing, reasoning.
                Coding tasks come back as a runnable project with a downloadable zip.
              </motion.p>
            </motion.div>
          ) : (
            <div className="msg-list">
              {active.messages.map((m, i) => (
                <Message
                  key={m.id}
                  msg={m}
                  /* Entry number in the record. 1-based and sequential across
                     the whole conversation, so the margin column reads as a
                     continuous numbered document. */
                  entryNo={i + 1}
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
          toolbar={composerToolbar}
        />
      </main>
    </div>
  )
}
