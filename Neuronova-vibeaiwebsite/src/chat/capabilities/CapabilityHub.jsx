import { useCallback, useEffect, useMemo, useState } from 'react'
import CapabilityDetail from './CapabilityDetail.jsx'
import CapabilityEditor from './CapabilityEditor.jsx'
import CapabilityImportFlow from './CapabilityImportFlow.jsx'
import PendingActionInbox from './PendingActionInbox.jsx'
import ServiceConnections from './ServiceConnections.jsx'
import { capabilityApi } from './capabilityApi.js'
import './capability.css'

const SECTIONS = ['discover', 'installed', 'bundles', 'create', 'import', 'activity']

export default function CapabilityHub() {
  const [items, setItems] = useState([])
  const [section, setSection] = useState('discover')
  const [selected, setSelected] = useState(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const refresh = useCallback(async () => {
    try { setItems((await capabilityApi.list()).items || []); setNotice('') }
    catch (err) { setNotice(err.message) }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const shown = useMemo(() => items.filter((item) => {
    const manifest = item.manifest
    if (section === 'bundles' && manifest.kind !== 'bundle') return false
    if (section === 'installed' && !item.installed) return false
    const haystack = `${manifest.name} ${manifest.description} ${manifest.supported_tasks.join(' ')}`.toLowerCase()
    return haystack.includes(query.toLowerCase())
  }), [items, query, section])

  const install = async (id) => {
    setBusy(id)
    try { await capabilityApi.install(id); setNotice('Capability installed.'); await refresh() }
    catch (err) { setNotice(err.message) }
    finally { setBusy('') }
  }

  if (selected) return <div className="cap-shell"><CapabilityDetail capability={selected} onBack={() => setSelected(null)} onInstall={install} busy={busy === selected.id} /></div>

  return (
    <main className="cap-shell">
      <header className="cap-topbar">
        <a href="#/chat" className="cap-brand">VIBE<span>AI</span></a>
        <nav><a href="#/chat">Chat</a><a href="#/projects">Projects</a><a className="is-current" href="#/capabilities">Capabilities</a></nav>
        <PendingActionInbox />
      </header>
      <section className="cap-hero">
        <p className="cap-eyebrow">Capability Hub</p>
        <h1>Give the whole AI team better ways to work.</h1>
        <p>Discover trusted skills, compose specialist bundles, or bring portable Claude and Codex instructions into VibeAI’s guarded runtime.</p>
        <button className="cap-primary" onClick={() => setSection('bundles')}>Start with a bundle</button>
      </section>
      <div className="cap-tabs" role="tablist" aria-label="Capability sections">
        {SECTIONS.map((item) => <button role="tab" aria-selected={section === item} className={section === item ? 'is-active' : ''} key={item} onClick={() => setSection(item)}>{item}</button>)}
      </div>
      {notice && <p className="cap-notice" role="status">{notice}</p>}
      {(section === 'discover' || section === 'installed' || section === 'bundles') && <>
        <label className="cap-search"><span>Search capabilities</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Research, coding, writing…" /></label>
        <section className="cap-grid" aria-label={`${section} capabilities`}>
          {shown.map((item) => <article className="cap-card" key={item.id}>
            <div><span className={`cap-trust is-${item.manifest.trust}`}>{item.manifest.trust.replaceAll('_', ' ')}</span><span className="cap-kind">{item.manifest.kind.replaceAll('_', ' ')}</span></div>
            <h2>{item.manifest.name}</h2><p>{item.manifest.description}</p>
            <div className="cap-tags">{item.manifest.supported_tasks.slice(0, 3).map((task) => <span key={task}>{task.replaceAll('_', ' ')}</span>)}</div>
            <div className="cap-card-actions"><button className="cap-secondary" onClick={() => setSelected(item)}>Inspect</button>{item.installed ? <span className="cap-installed">Installed</span> : <button className="cap-primary" disabled={busy === item.id} onClick={() => install(item.id)}>Install</button>}</div>
          </article>)}
          {shown.length === 0 && <div className="cap-empty"><h2>Nothing here yet</h2><p>Try another search, or create a safe instruction skill.</p></div>}
        </section>
      </>}
      {section === 'create' && <CapabilityEditor onDone={refresh} />}
      {section === 'import' && <CapabilityImportFlow />}
      {section === 'activity' && <ServiceConnections />}
    </main>
  )
}
