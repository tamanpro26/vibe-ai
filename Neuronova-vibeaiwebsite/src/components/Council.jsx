import { useEffect, useState } from 'react'
import SectionHeader from './SectionHeader.jsx'
import { COUNCIL_STAGES } from '../data.js'

export default function Council() {
  const [active, setActive] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setActive((a) => (a + 1) % COUNCIL_STAGES.length), 2400)
    return () => clearInterval(id)
  }, [])

  return (
    <section className="section" id="council">
      <div className="container">
        <SectionHeader index="03" label="REASONING" title="Five stages. Many models. One answer.">
          The Free Manager Council never trusts a single model's first attempt. Each answer runs a
          gauntlet where different free models plan, draft, attack, and rebuild it.
        </SectionHeader>
        <div className="council-track" data-reveal>
          {COUNCIL_STAGES.map((s, i) => (
            <div className={`council-stage${i === active ? ' is-active' : ''}`} key={s.name}>
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
