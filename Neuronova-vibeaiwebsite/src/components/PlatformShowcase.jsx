import { useRef } from 'react'
import { motion, useInView, useReducedMotion, useScroll, useTransform } from 'motion/react'
import StaggerReveal from './StaggerReveal.jsx'

const ROUTE_STAGES = [
  { label: 'Understand', meta: 'Prompt refiner', state: 'done' },
  { label: 'Plan', meta: 'Manager', state: 'done' },
  { label: 'Execute', meta: '3 specialists', state: 'live' },
  { label: 'Critique', meta: 'Independent review', state: 'next' },
  { label: 'Verify', meta: 'Final synthesis', state: 'next' },
]

const SPECIALISTS = [
  { name: 'Brain', detail: 'Reasoning', color: 'var(--agent-brain)' },
  { name: 'Code', detail: 'Implementation', color: 'var(--agent-code)' },
  { name: 'Research', detail: 'Evidence', color: 'var(--agent-vision)' },
]

export default function PlatformShowcase() {
  const sectionRef = useRef(null)
  const reduced = useReducedMotion()
  const isInView = useInView(sectionRef, { amount: 0.22 })
  const { scrollYProgress } = useScroll({
    target: sectionRef,
    offset: ['start end', 'end start'],
  })
  const frameY = useTransform(scrollYProgress, [0, 0.5, 1], [44, 0, -28])
  const frameScale = useTransform(scrollYProgress, [0, 0.5, 1], [0.975, 1, 0.985])

  return (
    <section className="platform-showcase" id="platform" ref={sectionRef}>
      <div className="platform-scanlines" aria-hidden="true" />
      <div className="container">
        <div className="platform-intro">
          <div>
            <p className="eyebrow"><span className="eyebrow-n">02</span> / LIVE SYSTEM</p>
            <StaggerReveal
              className="platform-title"
              text="One prompt becomes a <em>verified</em> piece of work."
            />
          </div>
          <div className="platform-intro-copy" data-reveal>
            <span className="platform-rule" aria-hidden="true" />
            <p>
              Watch VibeAI refine the request, assemble the right specialists, challenge their
              output, and return one answer. The interface exposes the process without turning
              the product into a dashboard maze.
            </p>
          </div>
        </div>

        <motion.div
          className={`platform-frame${isInView && !reduced ? ' is-active' : ''}`}
          style={reduced ? undefined : { y: frameY, scale: frameScale }}
          data-reveal
        >
          <div className="platform-frame-bar">
            <div className="platform-frame-brand">
              <span className="platform-frame-mark" aria-hidden="true" />
              VIBEAI / ORCHESTRATION RUN
            </div>
            <div className="platform-frame-status">
              <span aria-hidden="true" />
              system online
            </div>
            <span className="platform-frame-id">RUN_8A21</span>
          </div>

          <div className="platform-console" role="img" aria-label="A VibeAI request moving through planning, specialist execution, critique, and verification">
            <aside className="platform-route">
              <span className="platform-panel-label">ROUTE</span>
              <ol>
                {ROUTE_STAGES.map((stage, index) => (
                  <li className={`is-${stage.state}`} key={stage.label}>
                    <span className="platform-route-index">{String(index + 1).padStart(2, '0')}</span>
                    <span>
                      <strong>{stage.label}</strong>
                      <small>{stage.meta}</small>
                    </span>
                  </li>
                ))}
              </ol>
            </aside>

            <div className="platform-workspace">
              <div className="platform-prompt">
                <span>REQUEST</span>
                <p>Design the launch plan, challenge its assumptions, and produce the assets.</p>
                <small>BALANCED REASONING · AUTO ROUTE</small>
              </div>

              <div className="platform-flow" aria-hidden="true">
                <span className="platform-flow-line" />
                <span className="platform-flow-signal" />
                <span className="platform-flow-node platform-flow-node-a" />
                <span className="platform-flow-node platform-flow-node-b" />
                <span className="platform-flow-node platform-flow-node-c" />
              </div>

              <div className="platform-specialists">
                {SPECIALISTS.map((specialist, index) => (
                  <div className="platform-specialist" style={{ '--specialist': specialist.color }} key={specialist.name}>
                    <div className="platform-specialist-head">
                      <span className="platform-specialist-orb" aria-hidden="true" />
                      <small>0{index + 1}</small>
                    </div>
                    <strong>{specialist.name}</strong>
                    <span>{specialist.detail}</span>
                    <div className="platform-specialist-progress" aria-hidden="true"><i /></div>
                  </div>
                ))}
              </div>

              <div className="platform-verdict">
                <div>
                  <span className="platform-verdict-kicker">COUNCIL VERDICT</span>
                  <strong>Answer verified across three independent routes.</strong>
                </div>
                <div className="platform-score">
                  <span>CONFIDENCE</span>
                  <strong>94%</strong>
                </div>
              </div>
            </div>
          </div>
        </motion.div>

        <div className="platform-caption" data-reveal>
          <span>01 / Route with intent</span>
          <span>02 / Work in parallel</span>
          <span>03 / Critique before delivery</span>
        </div>
      </div>
    </section>
  )
}
