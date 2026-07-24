import { useState } from 'react'
import { makeZip } from './engine.js'

/* Minimal markdown: ``` fences, **bold**, `inline code`, - lists, headings. */

function Inline({ text }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
    if (p.startsWith('`') && p.endsWith('`')) return <code key={i}>{p.slice(1, -1)}</code>
    if (p.startsWith('*') && p.endsWith('*') && p.length > 2)
      return <em key={i}>{p.slice(1, -1)}</em>
    return p
  })
}

function CodeBlock({ lang, code }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="msg-codeblock">
      <div className="msg-codebar">
        <span>{lang || 'text'}</span>
        <button onClick={copy}>{copied ? '✓ copied' : 'copy'}</button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  )
}

function Markdown({ text }) {
  const blocks = []
  const fence = /```(\w*)\n([\s\S]*?)```/g
  let last = 0
  let m
  while ((m = fence.exec(text)) !== null) {
    if (m.index > last) blocks.push({ type: 'text', body: text.slice(last, m.index) })
    blocks.push({ type: 'code', lang: m[1], body: m[2] })
    last = m.index + m[0].length
  }
  if (last < text.length) blocks.push({ type: 'text', body: text.slice(last) })

  return blocks.map((b, bi) => {
    if (b.type === 'code') return <CodeBlock key={bi} lang={b.lang} code={b.body} />
    const lines = b.body.split('\n')
    const out = []
    let list = null
    lines.forEach((line, li) => {
      if (/^\s*[-*] /.test(line)) {
        list = list || []
        list.push(<li key={li}><Inline text={line.replace(/^\s*[-*] /, '')} /></li>)
        return
      }
      if (list) {
        out.push(<ul key={`ul${li}`}>{list}</ul>)
        list = null
      }
      if (/^\[\w+\]/.test(line)) {
        out.push(
          <p key={li} className="msg-sysline">
            <Inline text={line} />
          </p>,
        )
      } else if (/^#+ /.test(line)) {
        out.push(
          <p key={li} className="msg-heading">
            <Inline text={line.replace(/^#+ /, '')} />
          </p>,
        )
      } else if (line.trim()) {
        out.push(
          <p key={li}>
            <Inline text={line} />
          </p>,
        )
      }
    })
    if (list) out.push(<ul key="ul-end">{list}</ul>)
    return <div key={bi}>{out}</div>
  })
}

function ZipCard({ project }) {
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const blob = await makeZip(project.files, project.slug)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${project.slug}.zip`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="zip-card">
      <div className="zip-info">
        <span className="zip-icon" aria-hidden="true">⬇</span>
        <div>
          <span className="zip-name">{project.slug}.zip</span>
          <span className="zip-meta">{project.files.length} files · ready to run</span>
        </div>
      </div>
      <button className="zip-btn" onClick={download} disabled={busy}>
        {busy ? 'Packing…' : 'Download zip'}
      </button>
    </div>
  )
}

export default function Message({ msg, isStreaming, onRegenerate, canRegenerate }) {
  const [copied, setCopied] = useState(false)
  const copyAll = async () => {
    await navigator.clipboard.writeText(msg.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  const isUser = msg.role === 'user'

  return (
    <div className={`msg ${isUser ? 'msg-user' : 'msg-ai'}`}>
      <div className="msg-avatar" aria-hidden="true">
        {isUser ? 'U' : 'V'}
      </div>
      <div className="msg-body">
        <div className="msg-role">{isUser ? 'You' : 'VibeAI'}</div>
        {msg.attachments?.length > 0 && (
          <div className="msg-attachments">
            {msg.attachments.map((a, i) => (
              <span className="attach-chip" key={i}>
                📎 {a.name}
              </span>
            ))}
          </div>
        )}
        <div className="msg-content">
          {isUser ? (
            <p style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</p>
          ) : (
            <Markdown text={msg.content} />
          )}
          {isStreaming && <span className="msg-caret" aria-hidden="true" />}
        </div>
        {msg.project && !isStreaming && <ZipCard project={msg.project} />}
        {!isUser && !isStreaming && (
          <div className="msg-actions">
            <button onClick={copyAll}>{copied ? '✓ copied' : 'copy'}</button>
            {canRegenerate && <button onClick={onRegenerate}>↻ regenerate</button>}
          </div>
        )}
      </div>
    </div>
  )
}
