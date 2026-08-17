import { useEffect, useRef, useState } from 'react'
import { matchCapabilityCommands, parseCapabilityCommand } from './capabilities/commands.js'

export default function Composer({
  onSend,
  streaming,
  onStop,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  toolbar = null,
  capabilityCommands = [],
  slashCommandsEnabled = true,
  placeholder = 'Message VibeAI - code, research, writing, anything',
  hint = 'Enter to send | Shift+Enter for a new line | drag and drop files anywhere',
}) {
  const [text, setText] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [activeCommandIndex, setActiveCommandIndex] = useState(0)
  const taRef = useRef(null)
  const formRef = useRef(null)
  const fileRef = useRef(null)
  const folderRef = useRef(null)
  const menuRef = useRef(null)
  const commandMatches = slashCommandsEnabled
    ? matchCapabilityCommands(text, capabilityCommands)
    : []
  const commandMenuOpen = commandMatches.length > 0
  // Typing "/" used to render NOTHING when there were no installed
  // capabilities -- which is the normal state for a new account, and is
  // indistinguishable from the feature being broken. Reported as exactly that.
  // An empty result is information; show it rather than swallowing it.
  const slashTyped = slashCommandsEnabled && /^\/[^\s]*$/.test(text.trim())
  const emptyHint = !slashTyped ? null
    : capabilityCommands.length === 0
      ? 'No capabilities installed yet — add one in the Capability Hub to invoke it here.'
      : commandMatches.length === 0
        ? 'No installed capability matches that name.'
        : null
  // "/capability-id" with no request typed yet is not sendable -- send() bails
  // on it. Enter can't reach that (the open menu intercepts it), but the send
  // button could: it only checks text.trim(), so it stayed enabled and clicked
  // into a silent no-op. Disable it instead, so the control tells the truth.
  const awaitingCommandPrompt = slashCommandsEnabled
    && Boolean(parseCapabilityCommand(text, capabilityCommands)?.prompt === '')

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
    const invocation = slashCommandsEnabled
      ? parseCapabilityCommand(trimmed, capabilityCommands)
      : null
    if (invocation && !invocation.prompt) return
    try {
      const accepted = onSend(
        invocation?.prompt || trimmed,
        invocation ? { capabilityId: invocation.capabilityId } : {},
      )
      if (accepted !== false) {
        setText('')
        setActiveCommandIndex(0)
      }
    } catch (error) {
      // A synchronous persistence error must keep the user's draft intact.
      console.error('[composer] could not start message:', error)
    }
  }

  const chooseCommand = (command) => {
    setText(`/${command.capabilityId} `)
    setActiveCommandIndex(0)
    requestAnimationFrame(() => taRef.current?.focus())
  }

  const onSubmit = (event) => {
    event.preventDefault()
    send()
  }

  const onKeyDown = (event) => {
    if (commandMenuOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const offset = event.key === 'ArrowDown' ? 1 : -1
        setActiveCommandIndex((index) => (
          (index + offset + commandMatches.length) % commandMatches.length
        ))
        return
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        chooseCommand(commandMatches[activeCommandIndex] || commandMatches[0])
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setText(text.slice(1))
        setActiveCommandIndex(0)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      formRef.current?.requestSubmit()
    }
  }

  return (
    <div className="composer-wrap">
      {emptyHint && (
        <div className="slash-menu slash-menu-empty" role="status">
          <div className="slash-menu-head">
            <strong>Skills &amp; plugin features</strong>
            <a href="#/capabilities">Open Capability Hub →</a>
          </div>
          <p className="slash-empty-note">{emptyHint}</p>
        </div>
      )}
      {commandMenuOpen && (
        <div id="capability-command-menu" className="slash-menu" role="listbox" aria-label="Capability commands">
          <div className="slash-menu-head">
            <strong>Skills &amp; plugin features</strong>
            <span>Choose with ↑↓ · Enter</span>
          </div>
          {commandMatches.map((command, index) => (
            <button
              type="button"
              role="option"
              aria-selected={index === activeCommandIndex}
              className={`slash-command${index === activeCommandIndex ? ' is-active' : ''}`}
              key={command.capabilityId}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => chooseCommand(command)}
            >
              <code>/{command.capabilityId}</code>
              <span><strong>{command.name}</strong>{command.description}</span>
              <small>{command.kindLabel}</small>
            </button>
          ))}
        </div>
      )}
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
          onChange={(event) => {
            setText(event.target.value)
            setActiveCommandIndex(0)
          }}
          onKeyDown={onKeyDown}
          aria-expanded={commandMenuOpen}
          aria-controls={commandMenuOpen ? 'capability-command-menu' : undefined}
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
            <button
              type="submit"
              className="send-btn"
              disabled={!text.trim() || awaitingCommandPrompt}
              aria-label="Send message"
            >
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
