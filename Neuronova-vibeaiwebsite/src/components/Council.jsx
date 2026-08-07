import { useEffect, useRef, useState } from 'react'
import { useInView, useReducedMotion } from 'motion/react'
import SectionHeader from './SectionHeader.jsx'
import { COUNCIL_STAGES } from '../data.js'

export default function Council() {
  const [active, setActive] = useState(0)
  const sectionRef = useRef(null)
  const isInView = useInView(sectionRef, { amount: 0.15 })
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!isInView || reduced) return undefined
    const id = setInterval(() => setActive((a) => (a + 1) % COUNCIL_STAGES.length), 2400)
    return () => clearInterval(id)
  }, [isInView, reduced])

  return (
    <section className="section" id="council" ref={sectionRef}>
      <div className="container">
        <SectionHeader index="03" label="REASONING" title="Five stages. Many models. One answer.">
          The Free Manager Council never trusts a single model's first attempt. Each answer runs a
          gauntlet where different free models plan, draft, attack, and rebuild it.
        </SectionHeader>
        <div className="council-track" data-reveal>
          {COUNCIL_STAGES.map((s, i) => (
            <div className={`council-stage spotlight-card${i === active ? ' is-active' : ''}`} key={s.name}>
              <span className="council-n">{s.n}</span>
              <h3 className="council-name">{s.name}</h3>
              <p className="council-desc">{s.desc}</p>
              {i < COUNCIL_STAGES.length - 1 && <span className="council-link" aria-hidden="true" />}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
