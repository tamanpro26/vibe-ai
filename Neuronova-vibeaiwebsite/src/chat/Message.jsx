import { useState, useEffect } from 'react'
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

/* ── Thinking indicator ────────────────────────────────────────────────────
 * Replaces the old "[ROUTER] intent: general - Manager evaluating task" line.
 * That read as a log dump: it exposed internal plumbing, and it was static, so
 * a slow turn looked frozen.
 *
 * This instead advances through the stages a request actually passes through,
 * so the label carries real information about elapsed progress rather than
 * decorating a wait. The orb keeps moving even when the stage does not, which
 * is what distinguishes "still working" from "hung".
 */
const THINK_STAGES = [
  { at: 0, label: 'reading your message' },
  { at: 1600, label: 'routing to a specialist' },
  { at: 3600, label: 'gathering context' },
  { at: 7000, label: 'reasoning' },
  { at: 13000, label: 'composing the answer' },
  { at: 22000, label: 'still working, this one is big' },
]

function Thinking() {
  const [ms, setMs] = useState(0)
  useEffect(() => {
    const t0 = Date.now()
    const id = setInterval(() => setMs(Date.now() - t0), 200)
    return () => clearInterval(id)
  }, [])

  // Last stage whose threshold has passed.
  const stage = THINK_STAGES.reduce((acc, s) => (ms >= s.at ? s : acc), THINK_STAGES[0])
  const secs = Math.floor(ms / 1000)

  return (
    <div className="think" role="status" aria-live="polite">
      <span className="think-orb" aria-hidden="true">
        <span className="think-core" />
        <span className="think-ring" />
        <span className="think-ring think-ring-2" />
      </span>
      <span className="think-label" key={stage.label}>
        {stage.label}
      </span>
      {secs >= 3 && <span className="think-clock">{secs}s</span>}
    </div>
  )
}

/* ── Project preview ───────────────────────────────────────────────────────
 * Live render of the generated project plus a source view per file.
 *
 * The generator emits index.html, styles.css and app.js as SEPARATE files
 * that reference each other by relative path. An iframe fed through srcDoc
 * has no base URL, so `<link href="styles.css">` and `<script src="app.js">`
 * would silently resolve to nothing and the preview would render unstyled,
 * dead HTML. So the sibling assets are inlined into the document before it is
 * handed to the frame.
 *
 * Sandboxed to allow-scripts only: generated code runs, but it cannot reach
 * this origin's DOM, storage, or cookies. allow-same-origin is deliberately
 * NOT set -- combined with allow-scripts it would let the frame escape the
 * sandbox entirely.
 */
function buildPreviewDoc(files) {
  const find = (p) => files.find((f) => f.path === p)?.content || ''
  const html = find('index.html')
  if (!html) return null
  const css = find('styles.css')
  const js = find('app.js')

  let doc = html
  if (css) {
    doc = doc.replace(
      /<link[^>]*href=["']styles\.css["'][^>]*>/i,
      `<style>\n${css}\n</style>`,
    )
  }
  if (js) {
    doc = doc.replace(
      /<script[^>]*src=["']app\.js["'][^>]*><\/script>/i,
      `<script>\n${js}\n</script>`,
    )
  }
  return doc
}

function ProjectPreview({ project }) {
  const doc = buildPreviewDoc(project.files)
  const [tab, setTab] = useState(doc ? '__preview__' : project.files[0]?.path)

  const isPreview = tab === '__preview__'
  const activeFile = project.files.find((f) => f.path === tab)

  return (
    <div className="pv">
      <div className="pv-bar">
        <span className="pv-title">{project.slug}</span>
        <div className="pv-tabs">
          {doc && (
            <button
              className={`pv-tab${isPreview ? ' is-on' : ''}`}
              onClick={() => setTab('__preview__')}
            >
              Preview
            </button>
          )}
          {project.files.map((f) => (
            <button
              key={f.path}
              className={`pv-tab${tab === f.path ? ' is-on' : ''}`}
              onClick={() => setTab(f.path)}
            >
              {f.path}
            </button>
          ))}
        </div>
      </div>

      <div className="pv-stage">
        {isPreview && doc ? (
          <iframe
            className="pv-frame"
            title={`${project.slug} preview`}
            srcDoc={doc}
            sandbox="allow-scripts"
          />
        ) : (
          <pre className="pv-code">
            <code>{activeFile?.content}</code>
          </pre>
        )}
      </div>
    </div>
  )
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

/* ── Render bay ────────────────────────────────────────────────────────────
 * Image generation takes ~17s on the free keyless model, so the WAIT is the
 * design problem, not an afterthought. A spinner would also read as a generic
 * web app rather than the instrument panel this product actually is.
 *
 * So the bay behaves like a plate being developed: it opens at the true target
 * aspect ratio (no layout jump when the image lands), a scan bar sweeps it
 * while telemetry counts up, and the finished image RESOLVES through a wipe
 * with a bright leading edge instead of fading in.
 *
 * Every timing and axis comes from `sig` -- a deterministic hash of the prompt
 * (engine.js::renderSignature). The same prompt always develops identically,
 * different prompts visibly differ. That is what keeps this from being one
 * canned animation replayed for every image.
 */
function RenderBay({ image }) {
  const [state, setState] = useState('scanning')   // scanning -> resolving -> done | failed
  const [elapsed, setElapsed] = useState(0)
  const sig = image.sig || {}

  useEffect(() => {
    const t0 = Date.now()
    const tick = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 250)

    // Decode off-DOM so the element only mounts once pixels are ready --
    // otherwise the browser paints a half-loaded image mid-reveal.
    let alive = true
    const img = new Image()
    img.onload = () => {
      if (!alive) return
      setState('resolving')
      setTimeout(() => alive && setState('done'), sig.revealMs || 1100)
    }
    img.onerror = () => alive && setState('failed')
    img.src = image.url

    return () => {
      alive = false
      clearInterval(tick)
      img.onload = img.onerror = null
    }
  }, [image.url, sig.revealMs])

  const pending = state === 'scanning'
  const style = {
    '--bay-ratio': `${image.width} / ${image.height}`,
    '--sweep-ms': `${sig.sweepMs || 1800}ms`,
    '--reveal-ms': `${sig.revealMs || 1100}ms`,
    '--grain': sig.grain || 3,
  }

  return (
    <figure
      className={`render-bay is-${state} axis-${sig.axis || 'tb'} bracket-${sig.bracket ?? 0}`}
      style={style}
    >
      <div className="bay-frame">
        {pending && <span className="bay-scan" aria-hidden="true" />}
        {pending && <span className="bay-lattice" aria-hidden="true" />}

        {state !== 'failed' && state !== 'scanning' && (
          <img className="bay-img" src={image.url} alt={image.subject} />
        )}
        {state === 'resolving' && <span className="bay-edge" aria-hidden="true" />}

        {state === 'failed' && (
          <div className="bay-failed">
            <span className="bay-failed-tag">RENDER FAILED</span>
            <a href={image.url} target="_blank" rel="noreferrer">open directly</a>
          </div>
        )}

        <span className="bay-corner tl" aria-hidden="true" />
        <span className="bay-corner tr" aria-hidden="true" />
        <span className="bay-corner bl" aria-hidden="true" />
        <span className="bay-corner br" aria-hidden="true" />
      </div>

      <figcaption className="bay-meta">
        <span className="bay-tag">{pending ? 'RENDERING' : state === 'failed' ? 'ERROR' : 'RENDER'}</span>
        <span className="bay-subject">{image.subject}</span>
        <span className="bay-spec">
          {image.width}x{image.height}
          {pending && <span className="bay-clock"> · {elapsed}s</span>}
        </span>
      </figcaption>
    </figure>
  )
}

/* ── Source cards ──────────────────────────────────────────────────────────
 * Retrieved sources with their real images, rendered as citations rather than
 * decoration: every card links out, so a claim in the answer can be checked
 * against where it came from.
 *
 * Thumbnails are best-effort. Wikimedia occasionally serves a page with no
 * lead image, and a broken-image icon sitting in a citation list reads as an
 * error in the answer itself. So the image element renders only when a real
 * thumbnail URL exists, and any load failure collapses the card to text-only.
 */
function SourceCard({ src, index }) {
  const [broken, setBroken] = useState(false)
  const showImg = src.thumb && !broken

  return (
    <a
      className={`src-card${showImg ? '' : ' is-textonly'}`}
      href={src.url}
      target="_blank"
      rel="noreferrer"
      style={{ '--i': index }}
    >
      {showImg && (
        <span className="src-thumb">
          <img src={src.thumb} alt="" loading="lazy" onError={() => setBroken(true)} />
        </span>
      )}
      <span className="src-body">
        <span className="src-title">{src.title}</span>
        <span className="src-extract">{src.extract}</span>
        <span className="src-site">{src.site}</span>
      </span>
    </a>
  )
}

function SourceCards({ sources }) {
  return (
    <div className="src-block">
      <div className="src-head">
        <span className="src-tag">SOURCES</span>
        <span className="src-count">{sources.length} retrieved</span>
      </div>
      <div className="src-grid">
        {sources.map((s, i) => (
          <SourceCard key={s.url} src={s} index={i} />
        ))}
      </div>
    </div>
  )
}

export default function Message({ msg, isStreaming, onRegenerate, canRegenerate, onContinue }) {
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
          {/* Before any text arrives the turn has nothing to show, so the
              stage indicator stands in for the answer. Once tokens land it
              gives way to the caret so the two never compete. */}
          {isStreaming && !msg.content && <Thinking />}
          {isStreaming && msg.content && <span className="msg-caret" aria-hidden="true" />}
        </div>
        {/* A reply that hit its token ceiling used to just stop mid-sentence
            with nothing telling the reader it was cut off -- the exact bug
            this fixes. finish_reason "length" sets msg.truncated, and this
            is the only thing that tells the reader the answer isn't done. */}
        {msg.truncated && !isStreaming && (
          <div className="msg-truncated">
            <span>⚠ Response cut short by length limit.</span>
            <button onClick={() => onContinue(msg.id)}>Continue →</button>
          </div>
        )}
        {/* Mounted DURING streaming, unlike ZipCard: the render bay should be
            scanning while the copy types, so the slow fetch overlaps the text
            instead of starting after it. */}
        {msg.image && <RenderBay image={msg.image} />}
        {msg.sources?.length > 0 && !isStreaming && <SourceCards sources={msg.sources} />}
        {msg.project && !isStreaming && <ProjectPreview project={msg.project} />}
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
