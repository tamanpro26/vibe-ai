import { useEffect, useRef, useState } from 'react'
import { actionApi } from './capabilityApi.js'

export default function ActionConfirmation({ action, onClose, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const heading = useRef(null)
  useEffect(() => heading.current?.focus(), [])

  const decide = async (decision) => {
    setBusy(true)
    setError('')
    try {
      await (decision === 'approve'
        ? actionApi.approve(action.id, action.request_digest)
        : actionApi.deny(action.id))
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="cap-sheet-scrim" role="presentation" onMouseDown={onClose}>
      <section className="cap-sheet" role="dialog" aria-modal="true" aria-labelledby="action-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="cap-sheet-close" aria-label="Close confirmation" onClick={onClose}>×</button>
        <p className="cap-eyebrow">External action · confirmation required</p>
        <h2 id="action-title" tabIndex={-1} ref={heading}>Review one exact change</h2>
        <p>VibeAI will not contact the service until you approve this request.</p>
        <div className="cap-impact">
          <span>Operation</span><strong>{action.operation}</strong>
          <span>Changes</span><strong>{action.mutable_resources.join(', ')}</strong>
          <span>Shares</span><strong>{action.shared_data.join(', ')}</strong>
          <span>Expires</span><strong>{new Date(action.expires_at).toLocaleString()}</strong>
        </div>
        <details><summary>Exact payload</summary><pre>{JSON.stringify(action.arguments, null, 2)}</pre></details>
        <p className="cap-digest">Approval fingerprint: {action.request_digest}</p>
        {error && <p className="cap-error" role="alert">{error}</p>}
        <div className="cap-sheet-actions">
          <button className="cap-secondary" disabled={busy} onClick={() => decide('deny')}>Deny</button>
          <button className="cap-primary" disabled={busy} onClick={() => decide('approve')}>{busy ? 'Saving decision…' : 'Approve this action'}</button>
        </div>
      </section>
    </div>
  )
}
