import SectionHeader from './SectionHeader.jsx'
import StaggerReveal from './StaggerReveal.jsx'

const PROBLEM_CLAIM =
  "A workflow tied to <em>one model</em> inherits that model's limits. " +
  "Rate limit, outage, context mismatch, or weak first draft—the whole task feels it."

export default function Problem() {
  return (
    <section className="section" id="problem">
      <div className="container">
        <SectionHeader index="01" label="THE PREMISE" title="One vendor is one point of failure." />
        <div className="problem-grid">
          <div className="problem-col" data-reveal>
            <StaggerReveal text={PROBLEM_CLAIM} className="problem-big" />
          </div>
          <div className="problem-col" data-reveal style={{ transitionDelay: '0.12s' }}>
            <p>
              VibeAI routes work across free-first, optional premium, and local model paths. When
              one route is unavailable it can try a suitable fallback; for complex requests it
              adds a structured plan, critique, refinement, and verification instead of trusting
              one model's first draft.
            </p>
            <p className="problem-note">
              The practical result is fewer dead ends and a clearer record of how an answer was
              produced. Availability still depends on configured providers and their current
              quotas—fallback improves resilience; it does not promise perfect uptime.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
