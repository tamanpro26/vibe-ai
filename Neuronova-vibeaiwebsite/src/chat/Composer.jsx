import { useEffect, useRef, useState } from 'react'

export default function Composer({
  onSend,
  streaming,
  onStop,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  toolbar = null,
  placeholder = 'Message VibeAI - code, research, writing, anything',
  hint = 'Enter to send | Shift+Enter for a new line | drag and drop files anywhere',
}) {
  const [text, setText] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const taRef = useRef(null)
  const formRef = useRef(null)
  const fileRef = useRef(null)
  const folderRef = useRef(null)
  const menuRef = useRef(null)

  useEffect(() => {
    const textarea = taRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
  }, [text])

  useEffect(() => {
    if (!menuOpen) return undefined
    const close = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const send = () => {
    const trimmed = text.trim()
    if (!trimmed || streaming) return
    try {
      const accepted = onSend(trimmed)
      if (accepted !== false) setText('')
    } catch (error) {
      // A synchronous persistence error must keep the user's draft intact.
      console.error('[composer] could not start message:', error)
    }
  }

  const onSubmit = (event) => {
    event.preventDefault()
    send()
  }

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      formRef.current?.requestSubmit()
    }
  }

  return (
    <div className="composer-wrap">
      {attachments.length > 0 && (
        <div className="composer-attachments">
          {attachments.map((attachment, index) => (
            <span className="attach-chip" key={`${attachment.name}${index}`}>
              {attachment.name}
              <button type="button" aria-label={`Remove ${attachment.name}`} onClick={() => onRemoveAttachment(index)}>
                x
              </button>
            </span>
          ))}
        </div>
      )}
      <form className="composer" ref={formRef} onSubmit={onSubmit}>
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          placeholder={placeholder}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-controls">
          <div className="composer-plus" ref={menuRef}>
            <button
              type="button"
              className="plus-btn"
              aria-label="Add files or folders"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              +
            </button>
            {menuOpen && (
              <div className="plus-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    fileRef.current?.click()
                    setMenuOpen(false)
                  }}
                >
                  Upload files
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    folderRef.current?.click()
                    setMenuOpen(false)
                  }}
                >
                  Upload folder
                </button>
              </div>
            )}
          </div>
          {toolbar && <div className="composer-toolbar">{toolbar}</div>}
          {streaming ? (
            <button type="button" className="send-btn stop" onClick={onStop} aria-label="Stop generating">
              Stop
            </button>
          ) : (
            <button type="submit" className="send-btn" disabled={!text.trim()} aria-label="Send message">
              Send
            </button>
          )}
        </div>
      </form>
      <p className="composer-hint">{hint}</p>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          onAddFiles(event.target.files)
          event.target.value = ''
        }}
      />
      <input
        ref={folderRef}
        type="file"
        webkitdirectory=""
        multiple
        hidden
        onChange={(event) => {
          onAddFiles(event.target.files)
          event.target.value = ''
        }}
      />
    </div>
  )
}
