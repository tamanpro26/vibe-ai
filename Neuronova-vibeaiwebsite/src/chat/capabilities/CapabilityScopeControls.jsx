import { useEffect, useState } from 'react'
import { capabilityApi } from './capabilityApi.js'

const OPTIONS = [
  ['inherit', 'Use account setting'],
  ['enabled', 'Always use here'],
  ['disabled', 'Never use here'],
]

export default function CapabilityScopeControls({ projectId, chatId }) {
  const [items, setItems] = useState([])
  const [choices, setChoices] = useState({})
  const [saving, setSaving] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        await capabilityApi.registerScope('project', projectId)
        if (chatId) await capabilityApi.registerScope('chat', chatId, projectId)
        const installed = ((await capabilityApi.list()).items || []).filter((item) => item.installed)
        if (!cancelled) {
          setItems(installed)
          setChoices(Object.fromEntries(installed.flatMap((item) => (
            (item.scope_overrides || [])
              .filter((scope) => (
                (scope.scope_kind === 'project' && scope.scope_id === projectId)
                || (scope.scope_kind === 'chat' && scope.scope_id === chatId)
              ))
              .map((scope) => [`${item.id}:${scope.scope_kind}`, scope.state])
          ))))
        }
      } catch (error) {
        if (!cancelled) setMessage(error.message)
      }
    }
    load()
    return () => { cancelled = true }
  }, [chatId, projectId])

  const change = async (item, scopeKind, scopeId, state) => {
    const key = `${item.id}:${scopeKind}`
    const previous = choices[key] || 'inherit'
    setChoices((current) => ({ ...current, [key]: state }))
    setSaving(key)
    setMessage('Saving capability preference…')
    try {
      await capabilityApi.setScope(item.installation_id, scopeKind, scopeId, state)
      setMessage('Capability preference saved.')
    } catch (error) {
      setChoices((current) => ({ ...current, [key]: previous }))
      setMessage(error.message)
    } finally {
      setSaving('')
    }
  }

  return (
    <section className="cap-scope-controls" aria-labelledby="cap-scope-title">
      <h3 id="cap-scope-title" className="proj-side-h3">Capabilities</h3>
      <p className="proj-side-hint">Tune installed skills for this project or only this chat.</p>
      {items.map((item) => (
        <div className="cap-scope-row" key={item.id}>
          <strong>{item.manifest.name}</strong>
          <ScopeSelect label="Project" disabled={saving === `${item.id}:project`} value={choices[`${item.id}:project`] || 'inherit'} onChange={(state) => change(item, 'project', projectId, state)} />
          {chatId && <ScopeSelect label="This chat" disabled={saving === `${item.id}:chat`} value={choices[`${item.id}:chat`] || 'inherit'} onChange={(state) => change(item, 'chat', chatId, state)} />}
        </div>
      ))}
      {items.length === 0 && !message && <p className="proj-side-hint">Install a capability to tune it here.</p>}
      {message && <p className="proj-side-hint" role="status">{message}</p>}
    </section>
  )
}

function ScopeSelect({ label, value, disabled, onChange }) {
  return <label><span>{label}</span><select disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>{OPTIONS.map(([option, text]) => <option value={option} key={option}>{text}</option>)}</select></label>
}
