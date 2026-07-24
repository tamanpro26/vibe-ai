import { useEffect, useState } from 'react'
import SectionHeader from './SectionHeader.jsx'
import { PROVIDERS } from '../data.js'

// Scripted loop: (active index, set of tripped indices, status line)
const SCRIPT = [
  { active: 0, tripped: [], msg: 'routing via google gemini — all circuits closed' },
  { active: 0, tripped: [], msg: 'routing via google gemini — all circuits closed' },
  { active: 1, tripped: [0], msg: '429 from google — circuit OPEN · failover to groq in 0ms' },
  { active: 1, tripped: [0], msg: 'routing via groq — google cooling down' },
  { active: 2, tripped: [0, 1], msg: 'groq quota hit — circuit OPEN · failover to cerebras' },
  { active: 2, tripped: [1], msg: 'google circuit CLOSED again — cerebras still serving' },
  { active: 0, tripped: [], msg: 'all circuits closed — back on primary. 0 requests dropped' },
]

export default function Failover() {
  const [step, setStep] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setStep((s) => (s + 1) % SCRIPT.length), 2100)
    return () => clearInterval(id)
  }, [])

  const { active, tripped, msg } = SCRIPT[step]

  return (
    <section className="section section-alt" id="resilience">
      <div className="container">
        <SectionHeader index="02" label="RESILIENCE" title="No single point of failure.">
          A circuit breaker tracks rate limits and outages per provider. When one trips, the
          request reroutes to the next available model — automatically, mid-task.
        </SectionHeader>
        <div className="failover-board" data-reveal>
          <div className="failover-grid" role="img" aria-label="Animated diagram: requests failing over between providers">
            {PROVIDERS.map((p, i) => {
              const isActive = i === active
              const isTripped = tripped.includes(i)
              return (
                <div
                  key={p.name}
                  className={`prov-chip${isActive ? ' is-active' : ''}${isTripped ? ' is-tripped' : ''}`}
                  style={{ '--prov': p.color }}
                >
                  <span className="prov-dot" aria-hidden="true" />
                  <span className="prov-name">{p.name}</span>
                  {p.optional && <span className="prov-opt">optional</span>}
                  <span className="prov-state">
                    {isTripped ? 'OPEN' : isActive ? 'SERVING' : 'READY'}
                  </span>
                </div>
              )
            })}
          </div>
          <p className="failover-status">
            <span className="status-dot" aria-hidden="true" />
            {msg}
          </p>
        </div>
      </div>
    </section>
  )
}
