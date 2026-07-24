import { useState } from 'react'
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
}) {
  const { user, logout } = useAuth()
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState('')

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
        <div className="sidebar-user">
          <span className="user-avatar" aria-hidden="true">
            {(user?.name || 'U')[0].toUpperCase()}
          </span>
          <div className="user-meta">
            <span className="user-name">{user?.name}</span>
            <span className="user-email">{user?.email}</span>
          </div>
          <button className="logout-btn" onClick={logout}>
            Log out
          </button>
        </div>
      </aside>
    </>
  )
}
