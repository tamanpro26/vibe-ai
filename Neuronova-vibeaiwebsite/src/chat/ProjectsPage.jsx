import { useEffect, useState } from 'react'
import { useAuth } from './auth.jsx'
import { buildProject, downloadProject, checkProjects } from './engine.js'
import { loadChats, saveChats } from './store.js'
import Sidebar from './Sidebar.jsx'
import SettingsModal from './SettingsModal.jsx'
import RegistryPanel from './RegistryPanel.jsx'

/*
 * Projects — its own page (#/projects), not a modal over the chat.
 *
 * Previously this lived as a dialog ChatApp opened over itself. Building
 * something real, watching it run for minutes, and downloading the result is
 * a distinct enough activity from chatting that it reads better as its own
 * place -- the same reasoning that already put the landing page and the chat
 * app on separate routes. The left rail (Sidebar) is shared across pages on
 * purpose: chat history, settings and the model registry stay reachable
 * everywhere, the same way a persistent app shell works in Claude or ChatGPT.
 *
 * The agent backend has no simulated tier (see engine.js::buildProject) --
 * that discipline carries over unchanged, just without the dialog chrome.
 */

const TASK_TYPES = [
  { value: 'coding', label: 'Software', hint: 'Libraries, CLIs, APIs, full apps' },
  { value: 'creative', label: 'Writing', hint: 'Documents and long-form copy' },
  { value: 'reasoning', label: 'Analysis', hint: 'Research and structured reasoning' },
]

const EXAMPLES = [
  'A Python CLI that reads a CSV and prints per-column summary statistics, with a pytest suite',
  'A responsive single-page portfolio site with a gallery and a validated contact form',
  'A FastAPI service with a /users endpoint, SQLite persistence and tests',
]

function fmtDuration(ms) {
  if (!ms) return '—'
  const s = ms / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function ProjectsPage() {
  const { user, getToken } = useAuth()
  const [chats, setChats] = useState(() => loadChats(user.id))
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [registryOpen, setRegistryOpen] = useState(false)
  const [available, setAvailable] = useState(false)

  const [task, setTask] = useState('')
  const [taskType, setTaskType] = useState('coding')
  const [building, setBuilding] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [project, setProject] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    const probe = () => checkProjects().then((ok) => alive && setAvailable(ok))
    probe()
    const id = setInterval(probe, 20000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  /* A build runs for minutes. Without a visible clock the page is
     indistinguishable from a hang, which is the single most common reason a
     user kills a long job that was about to succeed. */
  useEffect(() => {
    if (!building) return
    const started = Date.now()
    setElapsed(0)
    const id = setInterval(() => setElapsed(Date.now() - started), 100)
    return () => clearInterval(id)
  }, [building])

  const run = async () => {
    const trimmed = task.trim()
    if (!trimmed || building) return
    setBuilding(true)
    setError('')
    setProject(null)
    try {
      const token = await getToken()
      setProject(await buildProject(trimmed, token, taskType))
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBuilding(false)
    }
  }

  // Chat history stays visible from this page (shared Sidebar), but acting on
  // it here just switches to the chat page rather than reproducing ChatApp's
  // own conversation-selection state -- deep-linking a specific conversation
  // across a page navigation isn't worth the extra state plumbing for what
  // this page is for.
  const persist = (next) => {
    setChats(next)
    saveChats(user.id, next)
  }
  const goToChat = () => {
    window.location.hash = '#/chat'
  }
  const handleRename = (id, title) => persist(chats.map((c) => (c.id === id ? { ...c, title } : c)))
  const handleDelete = (id) => persist(chats.filter((c) => c.id !== id))

  const totalBytes = project ? project.files.reduce((n, f) => n + new Blob([f.content]).size, 0) : 0

  return (
    <div className="chat-app">
      <Sidebar
        chats={chats}
        activeId={null}
        onSelect={goToChat}
        onNew={goToChat}
        onRename={handleRename}
        onDelete={handleDelete}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenRegistry={() => setRegistryOpen(true)}
      />
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
      <RegistryPanel open={registryOpen} onClose={() => setRegistryOpen(false)} />

      <main className="chat-main">
        <header className="chat-top">
          <button
            className="hamburger"
            aria-label="Toggle chat history"
            onClick={() => setSidebarOpen((o) => !o)}
          >
            ☰
          </button>
          <span className="chat-title">Projects</span>
          <div className="chat-top-right">
            <span className={`engine-badge${available ? ' is-live' : ''}`}>
              {available ? 'AGENT ONLINE' : 'AGENT OFFLINE'}
            </span>
          </div>
        </header>

        <div className="chat-scroll">
          <div className="proj-page">
            <p className="proj-lede">
              The coding agent writes real files, runs real commands and has to pass its own
              build gate before it hands anything back. Download the result as a zip.
            </p>

            {!available && (
              <p className="proj-offline" role="status">
                The agent backend is not reachable right now, so nothing can be built. This
                surface has no simulated mode on purpose — a project that was never built and
                never verified would not be a project.
              </p>
            )}

            <label className="set-field">
              <span className="set-field-label">What should it build?</span>
              <textarea
                className="set-input proj-textarea"
                rows={3}
                value={task}
                disabled={building || !available}
                onChange={(e) => setTask(e.target.value)}
                placeholder="Describe the project. Be specific about what it must do."
              />
            </label>

            {!task && (
              <div className="proj-examples">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    className="proj-example"
                    disabled={!available}
                    onClick={() => setTask(ex)}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}

            <div className="proj-types" role="radiogroup" aria-label="Project kind">
              {TASK_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  role="radio"
                  aria-checked={taskType === t.value}
                  disabled={building || !available}
                  className={`proj-type${taskType === t.value ? ' is-active' : ''}`}
                  onClick={() => setTaskType(t.value)}
                >
                  <span className="proj-type-label">{t.label}</span>
                  <span className="proj-type-hint">{t.hint}</span>
                </button>
              ))}
            </div>

            <div className="proj-actions">
              <button
                type="button"
                className="proj-build"
                onClick={run}
                disabled={building || !task.trim() || !available}
              >
                {building ? 'Building…' : 'Build project'}
              </button>
              {building && (
                <span className="proj-elapsed" role="status" aria-live="polite">
                  <span className="proj-dot" aria-hidden="true" />
                  agent running · {fmtDuration(elapsed)}
                </span>
              )}
            </div>

            {building && (
              <p className="proj-note">
                Real builds take minutes, not seconds. Leave this open — the result cannot be
                recovered from the browser if the connection drops.
              </p>
            )}

            {error && (
              <p className="proj-error" role="alert">
                {error}
              </p>
            )}

            {project && (
              <section className="proj-result">
                <div className="proj-result-head">
                  <h3 className="proj-h3">{project.slug}</h3>
                  <button type="button" className="proj-download" onClick={() => downloadProject(project)}>
                    Download .zip
                  </button>
                </div>

                <dl className="proj-stats">
                  <div>
                    <dt>Files</dt>
                    <dd>{project.files.length}</dd>
                  </div>
                  <div>
                    <dt>Size</dt>
                    <dd>{fmtBytes(totalBytes)}</dd>
                  </div>
                  <div>
                    <dt>Iterations</dt>
                    <dd>{project.iterations}</dd>
                  </div>
                  <div>
                    <dt>Commands</dt>
                    <dd>{project.commandsRun.length}</dd>
                  </div>
                  <div>
                    <dt>Time</dt>
                    <dd>{fmtDuration(project.totalMs)}</dd>
                  </div>
                </dl>

                {project.filesTruncated && (
                  <p className="proj-note">
                    Some files were too large to include and were left out of the zip.
                  </p>
                )}

                <ul className="proj-files">
                  {project.files.map((f) => (
                    <li key={f.path}>
                      <code className="proj-file-path">{f.path}</code>
                      <span className="proj-file-size">{fmtBytes(new Blob([f.content]).size)}</span>
                    </li>
                  ))}
                </ul>

                {project.summary && <p className="proj-summary">{project.summary}</p>}
              </section>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
