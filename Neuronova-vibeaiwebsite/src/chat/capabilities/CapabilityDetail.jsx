import { useEffect, useMemo, useState } from 'react'
import { actionApi, integrationRequest } from './capabilityApi.js'

export default function CapabilityDetail({ capability, onBack, onInstall, busy }) {
  const manifest = capability.manifest
  const [connections, setConnections] = useState([])
  const [action, setAction] = useState({ connection: '', repository: '', issue: '', body: '' })
  const [actionStatus, setActionStatus] = useState('')
  useEffect(() => {
    if (manifest.kind !== 'approved_action') return
    integrationRequest('list').then((value) => setConnections((value.items || []).filter((item) => !item.revoked))).catch(() => {})
  }, [manifest.kind])
  const connection = connections.find((item) => item.id === action.connection)
  const repositories = useMemo(() => (connection?.immutable_targets || []).map((item) => item.replace(/^repository:/, '')), [connection])
  const propose = async (event) => {
    event.preventDefault(); setActionStatus('Creating exact approval request…')
    try {
      await actionApi.propose({
        capability_version_id: capability.id,
        capability_digest: capability.content_digest,
        connection_id: action.connection,
        project_id: null,
        chat_id: null,
        operation: 'github.issue.comment',
        arguments: { repository_id: action.repository, issue_number: Number(action.issue), body: action.body },
        shared_data: ['comment_body'],
        mutable_resources: [`repository:${action.repository}:issue:${action.issue}:comments`],
        idempotency_key: `github-comment:${crypto.randomUUID()}`,
      })
      window.dispatchEvent(new Event('vibeai:actions-changed'))
      setActionStatus('Ready for review in the approval inbox. No external call has been made.')
    } catch (error) { setActionStatus(error.message) }
  }
  return (
    <article className="cap-detail" aria-labelledby="cap-detail-title">
      <button className="cap-back" onClick={onBack}>← Back to capabilities</button>
      <div className="cap-detail-head">
        <div>
          <span className={`cap-trust is-${manifest.trust}`}>{manifest.trust.replaceAll('_', ' ')}</span>
          <h2 id="cap-detail-title">{manifest.name}</h2>
          <p>{manifest.description}</p>
        </div>
        <button className="cap-primary" onClick={() => onInstall(capability.id)} disabled={busy}>
          {busy ? 'Installing…' : 'Install capability'}
        </button>
      </div>
      <dl className="cap-facts">
        <div><dt>Version</dt><dd>{manifest.version}</dd></div>
        <div><dt>Type</dt><dd>{manifest.kind.replaceAll('_', ' ')}</dd></div>
        <div><dt>Review</dt><dd>{capability.review_state.replaceAll('_', ' ')}</dd></div>
        <div><dt>Risk</dt><dd>{manifest.risk}</dd></div>
      </dl>
      <section className="cap-detail-section">
        <h3>Designed for</h3>
        <div className="cap-tags">{manifest.supported_tasks.map((task) => <span key={task}>{task.replaceAll('_', ' ')}</span>)}</div>
      </section>
      {capability.installed && manifest.kind === 'approved_action' && (
        <section className="cap-detail-section">
          <h3>Prepare a GitHub issue comment</h3>
          <p>Select only from repositories granted to your connected GitHub App. Submitting creates a review request; it does not post the comment.</p>
          {connections.length ? <form className="cap-action-form" onSubmit={propose}>
            <label>Connection<select required value={action.connection} onChange={(event) => setAction({ ...action, connection: event.target.value, repository: '' })}><option value="">Choose connection</option>{connections.map((item) => <option value={item.id} key={item.id}>{item.external_account_id}</option>)}</select></label>
            <label>Approved repository<select required value={action.repository} onChange={(event) => setAction({ ...action, repository: event.target.value })}><option value="">Choose repository ID</option>{repositories.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
            <label>Issue number<input required type="number" min="1" value={action.issue} onChange={(event) => setAction({ ...action, issue: event.target.value })} /></label>
            <label>Comment<textarea required maxLength="10000" rows="6" value={action.body} onChange={(event) => setAction({ ...action, body: event.target.value })} /></label>
            <button className="cap-primary">Create review request</button>
          </form> : <p className="cap-safe-note">Connect a GitHub App from Activity before preparing an action.</p>}
          {actionStatus && <p role="status">{actionStatus}</p>}
        </section>
      )}
      <section className="cap-detail-section">
        <h3>Permissions and services</h3>
        {manifest.permissions.length === 0 && manifest.services.length === 0 ? (
          <p className="cap-safe-note">Instruction only. This capability cannot call tools or external services.</p>
        ) : (
          <ul>
            {manifest.permissions.map((item) => <li key={item.name}>{item.purpose}</li>)}
            {manifest.services.map((item) => <li key={item.provider}>{item.provider}: {item.operations.join(', ')}</li>)}
          </ul>
        )}
      </section>
    </article>
  )
}
