import SectionHeader from './SectionHeader.jsx'

const STEPS = [
  {
    n: '1',
    title: 'Task in plain English',
    desc: '"Build a landing page", "fix this import bug" — no structured prompt required.',
  },
  {
    n: '2',
    title: 'Real work, real files',
    desc: 'The agent writes and edits actual files on disk and runs shell commands to build and test.',
  },
  {
    n: '3',
    title: 'Deterministic verifier battery',
    desc: 'Not vibes — checks. A deterministic pass scans the output for concrete defects:',
    checks: ['broken imports', 'placeholder stubs', 'dead images', 'unstyled CSS'],
  },
  {
    n: '4',
    title: 'Reflexion critic',
    desc: 'Failures loop back with a critique attached. The agent self-corrects and re-verifies.',
    loop: true,
  },
  {
    n: '5',
    title: 'Reports done — verified',
    desc: 'Only after the battery passes does the agent call the task complete.',
  },
]

export default function AgentLoop() {
  return (
    <section className="section section-alt" id="agent">
      <div className="container">
        <SectionHeader index="04" label="AUTONOMY" title="An agent that checks its own work.">
          <code className="inline-code">core/agent_loop.py</code> — an autonomous coding agent
          that treats "done" as something to prove, not declare.
        </SectionHeader>
        <ol className="agent-steps">
          {STEPS.map((s, i) => (
            <li
              className={`agent-step${s.loop ? ' has-loop' : ''}`}
              key={s.n}
              data-reveal
              style={{ transitionDelay: `${i * 0.08}s` }}
            >
              <span className="agent-n">{s.n}</span>
              <div className="agent-body">
                <h3 className="agent-title">{s.title}</h3>
                <p className="agent-desc">{s.desc}</p>
                {s.checks && (
                  <div className="agent-checks">
                    {s.checks.map((c) => (
                      <span className="check-chip" key={c}>
                        ✓ {c}
                      </span>
                    ))}
                  </div>
                )}
                {s.loop && <span className="agent-loop-tag">↺ loops back to step 2 until clean</span>}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}
