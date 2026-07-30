import SectionHeader from './SectionHeader.jsx'
import StaggerReveal from './StaggerReveal.jsx'

const PROBLEM_CLAIM =
  "Every strong coding assistant today rides on <em>one paid model</em> from <em>one vendor</em>. " +
  "Rate-limited? Outage? A bill you can't afford? You're stuck."

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
              VibeAI takes the opposite bet: orchestrate many <strong>free-tier</strong> models
              across many providers, so no single quota, outage, or invoice can stop the system.
              Then layer structured reasoning on top (planning, critique, verification) so the
              ensemble beats any one model's first attempt.
            </p>
            <p className="problem-note">
              Not a funded product. A solo engineering project, where the interesting part is the
              architecture and the discipline behind it.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
