import SectionHeader from './SectionHeader.jsx'

const CONTROLS = [
  {
    n: '01',
    title: 'Localhost-first API',
    text: 'The FastAPI server binds locally by default, keeping powerful execution routes off the public network unless you deliberately expose them.',
  },
  {
    n: '02',
    title: 'Token required when remote',
    text: 'Remote exposure requires bearer-token authentication. Provider credentials remain on the server, not in browser source.',
  },
  {
    n: '03',
    title: 'Guarded, not sandboxed',
    text: 'File and command operations include path checks, blocklists, bounded correction loops, and verification—but they are not a sandbox for untrusted instructions.',
  },
]

export default function Security() {
  return (
    <section className="section security-section" id="security">
      <div className="container">
        <SectionHeader index="08" label="SECURITY & CONTROL" title="Power stays inside an explicit boundary.">
          VibeAI can touch files and execute commands, so the honest security story is control,
          authentication, and careful deployment—not a promise that risk disappears.
        </SectionHeader>
        <div className="security-layout" data-reveal>
          <div className="security-principle">
            <span className="security-signal" aria-hidden="true" />
            <p>Default posture</p>
            <strong>Keep execution local. Authenticate remote access. Review powerful tasks.</strong>
            <span>Operators remain responsible for what they expose and which instructions they run.</span>
          </div>
          <ol className="security-controls">
            {CONTROLS.map((control) => (
              <li key={control.n}>
                <span>{control.n}</span>
                <div>
                  <h3>{control.title}</h3>
                  <p>{control.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}
