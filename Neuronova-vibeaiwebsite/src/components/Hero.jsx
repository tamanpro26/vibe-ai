import OrchestrationTrace from './OrchestrationTrace.jsx'
import CinematicField from './CinematicField.jsx'

export default function Hero({ motionMode, cinematicReady }) {
  return (
    <section className="cd-hero" id="top" aria-labelledby="command-deck-title">
      <CinematicField motionMode={motionMode} cinematicReady={cinematicReady} />
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

        <OrchestrationTrace motionMode={motionMode} />
      </div>
    </section>
  )
}
