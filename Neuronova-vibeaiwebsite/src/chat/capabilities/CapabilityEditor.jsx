import { useState } from 'react'
import { capabilityApi } from './capabilityApi.js'

const slug = (value) => value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export default function CapabilityEditor({ onDone }) {
  const [form, setForm] = useState({ name: '', description: '', instructions: '', tasks: 'writing' })
  const [status, setStatus] = useState('')
  const submit = async (event) => {
    event.preventDefault(); setStatus('Validating…')
    try {
      const manifest = {
        schema_version: 'vibeai.capability/v1', capability_id: slug(form.name), name: form.name,
        description: form.description, version: '1.0.0', kind: 'instruction_skill',
        supported_tasks: form.tasks.split(',').map((item) => item.trim()).filter(Boolean),
        activation: { mode: 'manual_only', intent_tags: [] }, permissions: [], services: [], dependencies: [],
        trust: 'user_imported', risk: 'low', instructions: form.instructions,
      }
      const draft = await capabilityApi.createDraft(manifest)
      await capabilityApi.publish(draft.id)
      setStatus('Published safely. It can now be installed.')
      onDone()
    } catch (err) { setStatus(err.message) }
  }
  return (
    <form className="cap-author" onSubmit={submit}>
      <h2>Create an instruction skill</h2>
      <p>Instructions can guide the AI team, but cannot grant tools, credentials, or external actions.</p>
      <label>Name<input required maxLength="120" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
      <label>Description<textarea required maxLength="1000" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
      <label>Tasks, comma separated<input value={form.tasks} onChange={(e) => setForm({ ...form, tasks: e.target.value })} /></label>
      <label>Instructions<textarea required rows="9" maxLength="100000" value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} /></label>
      <button className="cap-primary">Validate and publish</button>
      {status && <p role="status">{status}</p>}
    </form>
  )
}
