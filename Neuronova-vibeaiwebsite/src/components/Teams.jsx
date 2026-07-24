import SectionHeader from './SectionHeader.jsx'
import { TEAMS } from '../data.js'

export default function Teams() {
  return (
    <section className="section" id="teams">
      <div className="container">
        <SectionHeader index="05" label="ORGANIZATION" title="Structured like a company.">
          Specialist teams, each with its own Leader review step — and a CEO-style oversight
          report generated on demand from real operational logs.
        </SectionHeader>
        <div className="teams-grid">
          {TEAMS.map((t, i) => (
            <div
              className="team-card"
              key={t.name}
              style={{ '--team': t.color, transitionDelay: `${i * 0.07}s` }}
              data-reveal
            >
              <span className="team-swatch" aria-hidden="true" />
              <h3 className="team-name">{t.name}</h3>
              <p className="team-desc">{t.desc}</p>
              <span className="team-leader">LEADER REVIEW ✓</span>
            </div>
          ))}
        </div>
        <div className="ceo-card" data-reveal>
          <div className="ceo-left">
            <span className="ceo-tag">CEO OVERSIGHT</span>
            <p>
              An on-demand executive report answers one question — <em>is the system performing
              well?</em> — compiled from real operational logs, not marketing copy.
            </p>
          </div>
          <div className="ceo-right" aria-hidden="true">
            <span className="ceo-metric">
              <span className="ceo-metric-label">reads</span> failover events
            </span>
            <span className="ceo-metric">
              <span className="ceo-metric-label">reads</span> verifier outcomes
            </span>
            <span className="ceo-metric">
              <span className="ceo-metric-label">reads</span> team throughput
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
