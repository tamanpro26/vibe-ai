import SectionHeader from './SectionHeader.jsx'
import { PROVIDERS, WEBSITE_METRICS } from '../data.js'
import CountUp from './CountUp.jsx'

const NUMBERS = [
  { n: WEBSITE_METRICS.collectedTests, label: 'collected tests', note: 'offline-first verification across core orchestration paths' },
  { n: WEBSITE_METRICS.registrySlots, label: 'model registry slots', note: 'model × provider × role slots for redundancy' },
  { n: WEBSITE_METRICS.providerRoutes, label: 'provider routes', note: 'cloud, gateway, and local paths in the current registry' },
  { n: '2', label: 'interfaces', note: 'Rich terminal UI + FastAPI REST/WebSocket server' },
]

const STACK = ['Python', 'FastAPI', 'WebSocket', 'Rich TUI', 'pytest', 'asyncio']

export default function Engineering() {
  return (
    <section className="section section-alt" id="engineering">
      <div className="container">
        <SectionHeader index="07" label="ENGINEERING" title="Tested, not just demoed.">
          The current repository collects {WEBSITE_METRICS.collectedTests} tests, with core
          verification designed to run offline rather than depending on a healthy provider
          during every check.
        </SectionHeader>
        <div className="num-grid">
          {NUMBERS.map((x, i) => (
            <div className="num-card spotlight-card" key={x.label} data-reveal style={{ transitionDelay: `${i * 0.08}s` }}>
              <span className="num-big">
                <CountUp value={x.n} />
              </span>
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
              Proprietary codebase, all rights reserved. The API server is a code-execution
              surface by design and binds to localhost only; there is deliberately no public
              live demo.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
