import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { groupChats } from './store.js'
import { useAuth } from './auth.jsx'

export default function Sidebar({
  chats,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  open,
  onClose,
  onOpenSettings,
}) {
  const { user, logout } = useAuth()
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)

  // Dismiss the account menu on any outside click or Escape. A menu that only
  // closes via its own trigger is the usual bug here: click elsewhere and it
  // stays open, floating over the app.
  useEffect(() => {
    if (!menuOpen) return
    const close = () => setMenuOpen(false)
    const onKey = (e) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const filtered = query.trim()
    ? chats.filter((c) => c.title.toLowerCase().includes(query.toLowerCase()))
    : chats

  const commitRename = (id) => {
    if (draft.trim()) onRename(id, draft.trim())
    setEditingId(null)
  }

  return (
    <>
      {open && <div className="sidebar-scrim" onClick={onClose} aria-hidden="true" />}
      <aside className={`sidebar${open ? ' is-open' : ''}`}>
        <div className="sidebar-top">
          <a className="sidebar-brand" href="#/">
            <svg className="nav-mark" viewBox="0 0 64 64" aria-hidden="true">
              <g stroke="currentColor" strokeWidth="4" fill="none">
                <path d="M32 8 L53 20 L53 44 L32 56 L11 44 L11 20 Z" />
              </g>
              <circle cx="32" cy="32" r="6" fill="currentColor" />
            </svg>
            VIBE<span className="nav-brand-accent">AI</span>
          </a>
          <button className="newchat-btn" onClick={onNew}>
            + New chat
          </button>
          <input
            className="sidebar-search"
            type="search"
            placeholder="Search chats…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <nav className="sidebar-list" aria-label="Chat history">
          {filtered.length === 0 && <p className="sidebar-empty">No chats yet.</p>}
          {groupChats(filtered).map(([label, list]) => (
            <div className="chat-group" key={label}>
              <p className="chat-group-label">{label}</p>
              {list.map((c) => (
                <div
                  key={c.id}
                  className={`chat-item${c.id === activeId ? ' is-active' : ''}`}
                >
                  {editingId === c.id ? (
                    <input
                      className="chat-rename"
                      value={draft}
                      autoFocus
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => commitRename(c.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename(c.id)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                    />
                  ) : (
                    <>
                      <button className="chat-item-title" onClick={() => onSelect(c.id)}>
                        {c.title}
                      </button>
                      <span className="chat-item-tools">
                        <button
                          aria-label={`Rename ${c.title}`}
                          onClick={() => {
                            setEditingId(c.id)
                            setDraft(c.title)
                          }}
                        >
                          ✎
                        </button>
                        <button aria-label={`Delete ${c.title}`} onClick={() => onDelete(c.id)}>
                          🗑
                        </button>
                      </span>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>
        {/* One avatar row. Settings and Log out live behind the ⋯ menu:
            previously both were full buttons competing with the avatar and
            two text lines inside 264px, which truncated the name and email
            to "TA…" over "ta…". */}
        <div className="sidebar-user" onClick={() => setMenuOpen((v) => !v)}>
          <span className="user-avatar" aria-hidden="true">
            {(user?.name || 'U')[0].toUpperCase()}
          </span>
          <div className="user-meta">
            <span className="user-name">{user?.name}</span>
            <span className="user-email">{user?.email}</span>
          </div>
          <button
            className="user-menu-btn"
            aria-label="Account menu"
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
                className="user-menu"
                role="menu"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 4 }}
                transition={{ duration: 0.14, ease: [0.22, 0.85, 0.28, 1] }}
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    onOpenSettings()
                  }}
                >
                  Settings
                </button>
                <button
                  role="menuitem"
                  className="is-danger"
                  onClick={() => {
                    setMenuOpen(false)
                    logout()
                  }}
                >
                  Log out
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </aside>
    </>
  )
}
