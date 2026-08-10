import { useRef } from 'react'
import { motion, useScroll, useTransform } from 'motion/react'

const CAPABILITIES = [
  {
    number: '01',
    title: 'Transparent routing',
    detail: 'The workspace shows which engine tier answered, so the route never disappears behind the interface.',
    label: 'VISIBLE SYSTEM STATE',
  },
  {
    number: '02',
    title: 'Context that travels',
    detail: 'Profile and custom instructions reach every live engine tier instead of being stranded in one provider.',
    label: 'CONSISTENT DIRECTION',
  },
  {
    number: '03',
    title: 'Recovery by design',
    detail: 'Failed routes can fall through, and truncated replies can continue instead of posing as complete work.',
    label: 'RESILIENT DELIVERY',
  },
]

function CapabilityCards() {
  return CAPABILITIES.map((capability) => (
    <article className="cd-capability-card" key={capability.number}>
      <header>
        <span>{capability.number}</span>
        <small>{capability.label}</small>
      </header>
      <h3>{capability.title}</h3>
      <p>{capability.detail}</p>
      <span className="cd-capability-line" aria-hidden="true" />
    </article>
  ))
}

function AnimatedCapabilityGrid({ sectionRef }) {
  const { scrollYProgress } = useScroll({
    target: sectionRef,
    offset: ['start end', 'end start'],
  })
  const panelY = useTransform(scrollYProgress, [0, 0.5, 1], [32, 0, -20])

  return (
    <motion.div className="cd-capability-grid" style={{ y: panelY }} data-reveal>
      <CapabilityCards />
    </motion.div>
  )
}

export default function PlatformShowcase({ motionMode = 'full' }) {
  const sectionRef = useRef(null)

  return (
    <section className="cd-capabilities" id="platform" ref={sectionRef} aria-labelledby="capabilities-title">
      <div className="container">
        <div className="cd-capabilities-intro">
          <p className="cd-section-index">02 / PLATFORM PROOF</p>
          <h2 id="capabilities-title">The orchestration stays visible after the spectacle.</h2>
          <p>
            VibeAI exposes how work is routed, preserves the direction you gave it, and recovers
            when an engine cannot finish the job.
          </p>
        </div>

        {motionMode === 'full' ? (
          <AnimatedCapabilityGrid sectionRef={sectionRef} />
        ) : (
          <div className="cd-capability-grid is-in" data-reveal>
            <CapabilityCards />
          </div>
        )}
      </div>
    </section>
  )
}
