const DECK_STAGES = [
  { label: 'Plan', detail: 'Intent and constraints mapped', state: 'complete' },
  { label: 'Route', detail: 'Specialist agents assigned', state: 'complete' },
  { label: 'Critique', detail: 'Leadership review resolved', state: 'complete' },
  { label: 'Verify', detail: 'Final answer checked', state: 'verified' },
]

const SPECIALIST_ROLES = [
  { name: 'Research', detail: 'Ground the evidence', token: 'brain' },
  { name: 'Code', detail: 'Build the solution', token: 'code' },
  { name: 'Leadership', detail: 'Challenge the output', token: 'vision' },
]

export default function Hero() {
  return (
    <section className="cd-hero" id="top" aria-labelledby="command-deck-title">
      <div className="cd-hero-glow" aria-hidden="true" />
      <div className="container cd-hero-layout">
        <div className="cd-hero-copy">
          <p className="cd-kicker">VIBEAI / MULTI-AGENT ORCHESTRATION</p>
          <h1 id="command-deck-title">
            Complex work,
            <span>conducted across models.</span>
          </h1>
          <p className="cd-hero-summary">
            A manager assembles specialist agents, challenges their work, and verifies the result
            before delivery. One request becomes coordinated work—not a single-model reply.
          </p>

          <div className="cd-hero-actions">
            <a className="cta-primary" href="#/chat">
              Open the AI workspace
            </a>
            <a className="cta-secondary" href="#command-deck">
              Trace a request
              <span aria-hidden="true">↘</span>
            </a>
          </div>

          <ul className="cd-hero-signals" aria-label="Orchestration guarantees">
            <li><span aria-hidden="true" />Manager-led</li>
            <li><span aria-hidden="true" />Specialist teams</li>
            <li><span aria-hidden="true" />Verifier pass</li>
          </ul>
        </div>

        <article className="cd-deck" id="command-deck" aria-labelledby="command-deck-label">
          <header className="cd-deck-header">
            <div>
              <span className="cd-deck-overline">ILLUSTRATIVE TRACE</span>
              <h2 id="command-deck-label">The team at work</h2>
            </div>
            <span className="cd-deck-ready"><i aria-hidden="true" />trace ready</span>
          </header>

          <div className="cd-deck-request">
            <span>REQUEST / 01</span>
            <p>Research the decision, build the solution, challenge the assumptions, and verify it.</p>
          </div>

          <div className="cd-deck-system">
            <div className="cd-deck-core" aria-label="VibeAI manager coordinating specialist agents">
              <span className="cd-deck-core-orbit" aria-hidden="true" />
              <strong>V</strong>
              <small>MANAGER</small>
            </div>

            <ul className="cd-deck-agents" aria-label="Assigned specialist agents">
              {SPECIALIST_ROLES.map((role, index) => (
                <li className={`cd-agent cd-agent-${role.token}`} key={role.name}>
                  <span className="cd-agent-index">0{index + 1}</span>
                  <span>
                    <strong>{role.name}</strong>
                    <small>{role.detail}</small>
                  </span>
                  <i aria-hidden="true" />
                </li>
              ))}
            </ul>
          </div>

          <ol className="cd-deck-stages" aria-label="Orchestration stages">
            {DECK_STAGES.map((stage, index) => (
              <li className={`is-${stage.state}`} key={stage.label}>
                <span className="cd-stage-index">{String(index + 1).padStart(2, '0')}</span>
                <span>
                  <strong>{stage.label}</strong>
                  <small>{stage.detail}</small>
                </span>
              </li>
            ))}
          </ol>

          <footer className="cd-deck-verdict">
            <span className="cd-verdict-mark" aria-hidden="true">✓</span>
            <span>
              <small>VERIFIED OUTCOME</small>
              <strong>Specialists aligned. Checks complete.</strong>
            </span>
          </footer>
        </article>
      </div>
    </section>
  )
}
