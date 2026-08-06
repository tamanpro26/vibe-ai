import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useAuth } from './auth.jsx'
import { loadProjects, saveProjects, newProject, visibleProjects, SORTS } from './projectStore.js'
import Sidebar from './Sidebar.jsx'
import SettingsModal from './SettingsModal.jsx'
import RegistryPanel from './RegistryPanel.jsx'
import ListboxSelect from './ListboxSelect.jsx'

/*
 * Projects — #/projects, the grid. Each card is a persistent, revisitable
 * container (its own chats, instructions, memory, and files) rather than a
 * one-shot build form -- opening a card goes to #/projects/<id>. Creation
 * lives here, not in the sidebar, matching the reference: the sidebar nav
 * item is a plain link to this grid.
 *
 * Layout follows the reference screenshots (header row with search / sort /
 * primary action, then a responsive card grid) but is rendered in this
 * project's own 2A tokens rather than the reference's warm-dark palette, so
 * Projects stays visually continuous with the landing page, chat and
 * registry.
 */

function fmtRelative(ts) {
  const s = (Date.now() - ts) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  const d = Math.floor(s / 86400)
  if (d === 1) return '1 day ago'
  if (d < 30) return `${d} days ago`
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function CreateProjectModal({ open, onClose, onCreate }) {
  const reduceMotion = useReducedMotion()
  const dialogRef = useRef(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (!open) {
      setName('')
      setDescription('')
      return
    }
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const submit = () => {
    if (!name.trim()) return
    onCreate(name, description)
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="set-scrim"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14, ease: [0.22, 0.85, 0.28, 1] }}
        >
          <motion.div
            className="set-dialog proj-create-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Create a project"
            tabIndex={-1}
            ref={dialogRef}
            onClick={(e) => e.stopPropagation()}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.2, ease: [0.22, 0.85, 0.28, 1] }}
          >
            <div className="set-pane">
              <button className="set-close" onClick={onClose} aria-label="Cancel">
                ×
              </button>
              <h2 className="set-h2">Create a project</h2>

              <label className="set-field">
                <span className="set-field-label">What are you working on?</span>
                <input
                  className="set-input"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Name your project"
                  onKeyDown={(e) => e.key === 'Enter' && submit()}
                />
              </label>

              <label className="set-field">
                <span className="set-field-label">What are you trying to achieve?</span>
                <textarea
                  className="set-input proj-textarea"
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Describe your project, goals, subject, etc…"
                />
              </label>

              <div className="proj-create-actions">
                <button type="button" className="proj-cancel" onClick={onClose}>
                  Cancel
                </button>
                <button type="button" className="proj-build" disabled={!name.trim()} onClick={submit}>
                  Create project
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function ProjectCard({ project, onOpen, onToggleStar, onRename, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false)

  // Same dismissal contract as Sidebar's account menu: any outside click or
  // Escape closes it. A menu that only closes via its own trigger is the
  // usual bug here.
  useEffect(() => {
    if (!menuOpen) return
    const close = () => setMenuOpen(false)
    const onKey = (e) => e.key === 'Escape' && setMenuOpen(false)
    window.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const chatCount = project.chats?.length || 0

  return (
    <div className="proj-card">
      {/* The whole card is the click target, but it is a <button> rather than
          an <a> wrapping everything: the star and ⋮ controls are themselves
          interactive, and nesting buttons inside a link is invalid and breaks
          keyboard traversal. */}
      <button className="proj-card-hit" onClick={onOpen} aria-label={`Open ${project.name}`} />

      <div className="proj-card-top">
        <h3 className="proj-card-title">{project.name}</h3>
        <div className="proj-card-tools">
          <button
            type="button"
            className={`proj-star${project.starred ? ' is-on' : ''}`}
            aria-label={project.starred ? 'Unstar project' : 'Star project'}
            aria-pressed={!!project.starred}
            onClick={(e) => {
              e.stopPropagation()
              onToggleStar()
            }}
          >
            {project.starred ? '★' : '☆'}
          </button>
          <button
            type="button"
            className="proj-menu-btn"
            aria-label="Project actions"
            aria-expanded={menuOpen}
            onClick={(e) => {
              e.stopPropagation()
              setMenuOpen((v) => !v)
            }}
          >
            ⋯
          </button>
          <AnimatePresence>
            {menuOpen && (
              <motion.div
                className="proj-menu"
                role="menu"
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 2 }}
                transition={{ duration: 0.12, ease: [0.22, 0.85, 0.28, 1] }}
                onClick={(e) => e.stopPropagation()}
              >
                <button role="menuitem" onClick={() => { setMenuOpen(false); onRename() }}>
                  Rename
                </button>
                <button
                  role="menuitem"
                  className="is-danger"
                  onClick={() => { setMenuOpen(false); onDelete() }}
                >
                  Delete
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {project.description && <p className="proj-card-desc">{project.description}</p>}

      <div className="proj-card-meta">
        <span>{fmtRelative(project.updatedAt)}</span>
        {chatCount > 0 && <span>{chatCount} chat{chatCount === 1 ? '' : 's'}</span>}
        {project.files.length > 0 && (
          <span>{project.files.length} file{project.files.length === 1 ? '' : 's'}</span>
        )}
      </div>
    </div>
  )
}

export default function ProjectsListPage() {
  const { user } = useAuth()
  const [projects, setProjects] = useState(() => loadProjects(user.id))
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [registryOpen, setRegistryOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [sort, setSort] = useState('updated')
  const [storageError, setStorageError] = useState('')

  const shown = useMemo(() => visibleProjects(projects, query, sort), [projects, query, sort])

  const persist = (next) => {
    setProjects(next)
    const res = saveProjects(user.id, next)
    if (!res.ok) {
      setStorageError(
        res.quota
          ? 'Browser storage is full — that change was not saved.'
          : 'Could not write to browser storage — that change was not saved.',
      )
    }
  }

  const handleCreate = (name, description) => {
    const project = newProject(name, description)
    persist([project, ...projects])
    setCreateOpen(false)
    window.location.hash = `#/projects/${project.id}`
  }

  const goToChat = () => {
    window.location.hash = '#/chat'
  }

  return (
    <div className="chat-app">
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
      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={handleCreate}
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
          <span className="chat-title">Projects</span>
        </header>

        <div className="chat-scroll">
          <div className="proj-page">
            <div className="proj-header">
              <h1 className="proj-h1">Projects</h1>
              <div className="proj-header-tools">
                {searchOpen || query ? (
                  <input
                    className="set-input proj-search"
                    autoFocus
                    type="search"
                    value={query}
                    placeholder="Search projects…"
                    onChange={(e) => setQuery(e.target.value)}
                    onBlur={() => !query && setSearchOpen(false)}
                  />
                ) : (
                  <button
                    type="button"
                    className="proj-icon-btn"
                    aria-label="Search projects"
                    onClick={() => setSearchOpen(true)}
                  >
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                      <path d="M10.5 10.5 L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                    </svg>
                  </button>
                )}
                <ListboxSelect
                  className="proj-sort"
                  options={SORTS}
                  value={sort}
                  aria-label="Sort projects"
                  onChange={setSort}
                />
                <button type="button" className="proj-build" onClick={() => setCreateOpen(true)}>
                  New project
                </button>
              </div>
            </div>

            {storageError && (
              <p className="proj-error" role="alert">
                {storageError}
              </p>
            )}

            {projects.length === 0 ? (
              <div className="proj-empty">
                <h2>No projects yet</h2>
                <p>
                  A project is a persistent space with its own chats, instructions, memory and
                  files — create one to get started.
                </p>
                <button type="button" className="proj-build" onClick={() => setCreateOpen(true)}>
                  New project
                </button>
              </div>
            ) : shown.length === 0 ? (
              <p className="proj-side-hint">No project matches “{query}”.</p>
            ) : (
              <div className="proj-grid">
                {shown.map((p) => (
                  <ProjectCard
                    key={p.id}
                    project={p}
                    onOpen={() => {
                      window.location.hash = `#/projects/${p.id}`
                    }}
                    onToggleStar={() =>
                      persist(
                        projects.map((x) => (x.id === p.id ? { ...x, starred: !x.starred } : x)),
                      )
                    }
                    onRename={() => {
                      const name = window.prompt('Rename project', p.name)
                      if (name && name.trim()) {
                        persist(
                          projects.map((x) =>
                            x.id === p.id ? { ...x, name: name.trim(), updatedAt: Date.now() } : x,
                          ),
                        )
                      }
                    }}
                    onDelete={() => {
                      // Deleting a project destroys its chats and files with
                      // it, and there's no server-side copy to restore from --
                      // so this confirms rather than relying on undo.
                      if (window.confirm(`Delete “${p.name}” and everything in it?`)) {
                        persist(projects.filter((x) => x.id !== p.id))
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
