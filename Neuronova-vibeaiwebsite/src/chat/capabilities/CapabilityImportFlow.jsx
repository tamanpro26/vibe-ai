import { useState } from 'react'
import { capabilityApi } from './capabilityApi.js'

export default function CapabilityImportFlow() {
  const [form, setForm] = useState({ provider: 'claude', repository: '', sha: '' })
  const [status, setStatus] = useState('')
  const submit = async (event) => {
    event.preventDefault(); setStatus('Acquiring immutable source and scanning…')
    try {
      const result = await capabilityApi.importGitHub(form.provider, form.repository, form.sha)
      setStatus(`Quarantined for review · ${result.id}`)
    } catch (err) { setStatus(err.message) }
  }
  return (
    <form className="cap-author" onSubmit={submit}>
      <h2>Import from GitHub</h2>
      <p>VibeAI pins one commit, scans the package without running it, and keeps MCP, hooks, scripts, and provider-only powers disabled.</p>
      <label>Source format<select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}><option value="claude">Claude</option><option value="codex">Codex</option></select></label>
      <label>Repository (owner/name)<input required placeholder="owner/repository" value={form.repository} onChange={(e) => setForm({ ...form, repository: e.target.value })} /></label>
      <label>Full 40-character commit SHA<input required pattern="[0-9a-f]{40}" value={form.sha} onChange={(e) => setForm({ ...form, sha: e.target.value })} /></label>
      <button className="cap-primary">Acquire and scan</button>
      {status && <p role="status">{status}</p>}
    </form>
  )
}
