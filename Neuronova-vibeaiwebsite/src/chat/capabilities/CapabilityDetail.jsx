export default function CapabilityDetail({ capability, onBack, onInstall, busy }) {
  const manifest = capability.manifest
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
