import { useRef } from 'react'
import { motion, useInView, useReducedMotion } from 'motion/react'

const STEPS = [
  ['01', 'Map intent', 'Prompt refiner'],
  ['02', 'Assemble council', 'Specialist routing'],
  ['03', 'Challenge output', 'Independent critique'],
  ['04', 'Verify delivery', 'Evidence + checks'],
]

const METRICS = [
  ['Route confidence', '94%'],
  ['Specialists online', '03 / 03'],
  ['Checks complete', '12 / 12'],
]

export default function SignalTheater() {
  const ref = useRef(null)
  const reduced = useReducedMotion()
  const inView = useInView(ref, { amount: 0.28, once: true })

  return (
    <section className="signal-theater" ref={ref} aria-labelledby="signal-theater-title">
      <div className="container signal-theater-inner">
        <div className="signal-theater-copy" data-reveal>
          <p className="eyebrow"><span className="eyebrow-n">01</span> / SYSTEM IN MOTION</p>
          <h2 id="signal-theater-title">See the work before you trust the answer.</h2>
          <p>
            A visible run shows how VibeAI turns one request into specialist work, critique,
            and a checked final delivery.
          </p>
        </div>

        <motion.div
          className={`signal-console${inView && !reduced ? ' is-active' : ''}`}
          initial={reduced ? false : { opacity: 0, y: 36, scale: 0.975 }}
          animate={inView || reduced ? { opacity: 1, y: 0, scale: 1 } : undefined}
          transition={{ duration: 0.75, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="signal-console-bar">
            <span><i aria-hidden="true" /> VIBEAI / LIVE RUN</span>
            <span>RUN_8A21 · BALANCED</span>
          </div>
          <div className="signal-console-body">
            <ol className="signal-steps">
              {STEPS.map(([index, title, detail], stepIndex) => (
                <li key={title} style={{ '--step-delay': `${stepIndex * 150}ms` }}>
                  <span>{index}</span>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </li>
              ))}
            </ol>

            <div className="signal-core" aria-label="An animated VibeAI orchestration core">
              <span className="signal-orbit signal-orbit-a" aria-hidden="true" />
              <span className="signal-orbit signal-orbit-b" aria-hidden="true" />
              <span className="signal-beacon signal-beacon-a" aria-hidden="true" />
              <span className="signal-beacon signal-beacon-b" aria-hidden="true" />
              <div className="signal-core-mark" aria-hidden="true"><span>V</span></div>
              <p>COUNCIL<br /><strong>ACTIVE</strong></p>
            </div>

            <div className="signal-metrics">
              {METRICS.map(([label, value], index) => (
                <div className="signal-metric" key={label} style={{ '--metric-delay': `${280 + index * 130}ms` }}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                  <i aria-hidden="true"><b /></i>
                </div>
              ))}
            </div>
          </div>
          <div className="signal-console-foot">
            <span>INPUT / DESIGN A LAUNCH PLAN</span>
            <span className="signal-status"><i aria-hidden="true" /> checks running</span>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
