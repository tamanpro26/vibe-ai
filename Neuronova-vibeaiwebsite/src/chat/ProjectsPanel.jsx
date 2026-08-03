import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useAuth } from './auth.jsx'
import { buildProject, downloadProject } from './engine.js'

/*
 * Projects — ask the autonomous coding agent to BUILD something, then take the
 * result away as a zip.
 *
 * This is the one surface in the app with no simulated tier. Chat degrades down
 * an engine ladder because a degraded answer is still an answer; a project that
 * was never built and never verified is not a project, so when the agent
 * backend is unreachable this says so and stops. See engine.js::buildProject.
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

export default function ProjectsPanel({ open, onClose, available }) {
  const { getToken } = useAuth()
  const reduceMotion = useReducedMotion()
  const dialogRef = useRef(null)

  const [task, setTask] = useState('')
  const [taskType, setTaskType] = useState('coding')
  const [building, setBuilding] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [project, setProject] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      // Never let Escape discard a run that is still going — the work is real
      // and cannot be resumed from the browser.
      if (e.key === 'Escape' && !building) onClose()
    }
    window.addEventListener('keydown', onKey)
    dialogRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, building])

  /* A build runs for minutes. Without a visible clock the surface is
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

  const totalBytes = project
    ? project.files.reduce((n, f) => n + new Blob([f.content]).size, 0)
    : 0

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="set-scrim"
          onClick={() => !building && onClose()}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14, ease: [0.22, 0.85, 0.28, 1] }}
        >
          <motion.div
            className="set-dialog proj-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Projects"
            tabIndex={-1}
            ref={dialogRef}
            onClick={(e) => e.stopPropagation()}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.2, ease: [0.22, 0.85, 0.28, 1] }}
          >
            <div className="set-pane proj-pane">
              <button
                className="set-close"
                onClick={onClose}
                disabled={building}
                aria-label="Close projects"
              >
                ×
              </button>

              <h2 className="set-h2">Projects</h2>
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
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
