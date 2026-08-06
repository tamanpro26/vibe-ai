import { useEffect, useRef, useState } from 'react'

export default function Composer({
  onSend,
  streaming,
  onStop,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  // Optional controls rendered inside the composer, between the + button and
  // the send button (the reference puts its model selector here). Omitted by
  // ChatApp, which keeps its team/mode selectors up in the page header, so
  // this defaults to nothing and changes nothing for existing callers.
  toolbar = null,
  placeholder = 'Message VibeAI — code, research, writing, anything…',
  hint = 'Enter to send · Shift+Enter for a new line · drag & drop files anywhere',
}) {
  const [text, setText] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const taRef = useRef(null)
  const fileRef = useRef(null)
  const folderRef = useRef(null)
  const menuRef = useRef(null)

  // autosize textarea
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [text])

  // close plus-menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    const close = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const send = () => {
    const trimmed = text.trim()
    if (!trimmed || streaming) return
    onSend(trimmed)
    setText('')
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="composer-wrap">
      {attachments.length > 0 && (
        <div className="composer-attachments">
          {attachments.map((a, i) => (
            <span className="attach-chip" key={`${a.name}${i}`}>
              📎 {a.name}
              <button aria-label={`Remove ${a.name}`} onClick={() => onRemoveAttachment(i)}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="composer">
        <div className="composer-plus" ref={menuRef}>
          <button
            className="plus-btn"
            aria-label="Add files or folders"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            +
          </button>
          {menuOpen && (
            <div className="plus-menu" role="menu">
              <button
                role="menuitem"
                onClick={() => {
                  fileRef.current?.click()
                  setMenuOpen(false)
                }}
              >
                📄 Upload files
              </button>
              <button
                role="menuitem"
                onClick={() => {
                  folderRef.current?.click()
                  setMenuOpen(false)
                }}
              >
                📁 Upload folder
              </button>
            </div>
          )}
        </div>
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          placeholder={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {toolbar && <div className="composer-toolbar">{toolbar}</div>}
        {streaming ? (
          <button className="send-btn stop" onClick={onStop} aria-label="Stop generating">
            ◼
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={send}
            disabled={!text.trim()}
            aria-label="Send message"
          >
            ↑
          </button>
        )}
      </div>
      <p className="composer-hint">{hint}</p>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          onAddFiles(e.target.files)
          e.target.value = ''
        }}
      />
      <input
        ref={folderRef}
        type="file"
        webkitdirectory=""
        multiple
        hidden
        onChange={(e) => {
          onAddFiles(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
