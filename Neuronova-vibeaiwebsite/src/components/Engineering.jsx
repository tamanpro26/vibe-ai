import SectionHeader from './SectionHeader.jsx'
import { PROVIDERS } from '../data.js'

const NUMBERS = [
  { n: '439', label: 'automated tests', note: 'all offline & deterministic — engineered, not demoed' },
  { n: '38', label: 'model registry entries', note: 'model × provider × role slots for redundancy' },
  { n: '10', label: 'free-tier providers', note: 'no single quota can stop the system' },
  { n: '2', label: 'interfaces', note: 'Rich terminal UI + FastAPI REST/WebSocket server' },
]

const STACK = ['Python', 'FastAPI', 'WebSocket', 'Rich TUI', 'pytest', 'asyncio']

export default function Engineering() {
  return (
    <section className="section section-alt" id="engineering">
      <div className="container">
        <SectionHeader index="06" label="ENGINEERING" title="Tested, not just demoed.">
          The claim isn't "it works on my machine" — it's 439 deterministic tests that run with
          the network unplugged.
        </SectionHeader>
        <div className="num-grid">
          {NUMBERS.map((x, i) => (
            <div className="num-card" key={x.label} data-reveal style={{ transitionDelay: `${i * 0.08}s` }}>
              <span className="num-big">{x.n}</span>
              <span className="num-label">{x.label}</span>
              <span className="num-note">{x.note}</span>
            </div>
          ))}
        </div>
        <div className="eng-cols" data-reveal>
          <div className="eng-col">
            <h3 className="eng-col-title">PROVIDER MATRIX</h3>
            <ul className="prov-list">
              {PROVIDERS.map((p) => (
                <li key={p.name}>
                  <span className="prov-list-dot" style={{ background: p.color }} aria-hidden="true" />
                  {p.name}
                  {p.optional && <span className="prov-opt"> optional</span>}
                </li>
              ))}
            </ul>
          </div>
          <div className="eng-col">
            <h3 className="eng-col-title">STACK</h3>
            <div className="stack-chips">
              {STACK.map((s) => (
                <span className="stack-chip" key={s}>
                  {s}
                </span>
              ))}
            </div>
            <h3 className="eng-col-title eng-col-title-gap">STATUS</h3>
            <p className="eng-note">
              Proprietary codebase — all rights reserved. The API server is a code-execution
              surface by design and binds to localhost only; there is deliberately no public
              live demo.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
