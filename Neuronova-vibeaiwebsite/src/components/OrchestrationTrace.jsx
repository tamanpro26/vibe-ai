import { useEffect, useRef, useState } from 'react'

const TRACE_STAGES = [
  {
    key: 'planning',
    label: 'Planning',
    role: 'Strategy lead',
    detail: 'Maps the request, constraints, and success conditions.',
    status: 'Planning: Strategy lead maps the request and its constraints.',
  },
  {
    key: 'routing',
    label: 'Routing',
    role: 'Manager',
    detail: 'Assigns research, code, and leadership specialists.',
    status: 'Routing: Manager assigns the specialist team.',
  },
  {
    key: 'critique',
    label: 'Critique',
    role: 'Leadership reviewer',
    detail: 'Challenges assumptions and resolves conflicts.',
    status: 'Critique: Leadership reviewer challenges the combined work.',
  },
  {
    key: 'verification',
    label: 'Verification',
    role: 'Verifier',
    detail: 'Checks the final result before delivery.',
    status: 'Verification: Verifier checks the final result before delivery.',
  },
]

const SPECIALIST_ROLES = [
  { name: 'Research', detail: 'Ground the evidence', token: 'brain' },
  { name: 'Code', detail: 'Build the solution', token: 'code' },
  { name: 'Leadership', detail: 'Challenge the output', token: 'vision' },
]

const STAGE_DELAY = 1200

export default function OrchestrationTrace() {
  const [runStatus, setRunStatus] = useState('idle')
  const [activeStage, setActiveStage] = useState(null)
  const generationRef = useRef(0)
  const timerRef = useRef(null)

  useEffect(() => {
    if (runStatus !== 'running' || activeStage === null) return undefined

    const generation = generationRef.current
    timerRef.current = window.setTimeout(() => {
      if (generation !== generationRef.current) return
      if (activeStage === TRACE_STAGES.length - 1) {
        setRunStatus('complete')
        return
      }
      setActiveStage((stage) => stage + 1)
    }, STAGE_DELAY)

    return () => window.clearTimeout(timerRef.current)
  }, [activeStage, runStatus])

  useEffect(
    () => () => {
      generationRef.current += 1
      window.clearTimeout(timerRef.current)
    },
    [],
  )

  const begin = () => {
    generationRef.current += 1
    window.clearTimeout(timerRef.current)
    setActiveStage(0)
    setRunStatus('running')
  }

  const pause = () => {
    generationRef.current += 1
    window.clearTimeout(timerRef.current)
    setRunStatus('paused')
  }

  const resume = () => {
    generationRef.current += 1
    setRunStatus('running')
  }

  const inspect = (index) => {
    generationRef.current += 1
    window.clearTimeout(timerRef.current)
    setActiveStage(index)
    setRunStatus('paused')
  }

  const control = {
    idle: { label: 'Start trace', action: begin },
    running: { label: 'Pause trace', action: pause },
    paused: { label: 'Resume trace', action: resume },
    complete: { label: 'Replay trace', action: begin },
  }[runStatus]

  const liveMessage =
    runStatus === 'complete'
      ? 'Verification complete. Specialists aligned. Checks complete.'
      : activeStage === null
        ? 'Trace ready. Start to follow the illustrative request.'
        : TRACE_STAGES[activeStage].status

  return (
    <article
      className="cd-deck"
      id="command-deck"
      aria-labelledby="command-deck-label"
      data-run-status={runStatus}
    >
      <header className="cd-deck-header">
        <div>
          <span className="cd-deck-overline">SPECIALIST AGENTS / ILLUSTRATIVE TRACE</span>
          <h2 id="command-deck-label">The team at work</h2>
        </div>
        <span className="cd-deck-ready"><i aria-hidden="true" />{runStatus === 'complete' ? 'verified' : 'trace ready'}</span>
      </header>

      <div className="cd-deck-request">
        <span>REQUEST / 01</span>
        <p>Research the decision, build the solution, challenge the assumptions, and verify it.</p>
      </div>

      <div className="cd-deck-controls">
        <button type="button" onClick={control.action}>{control.label}</button>
        <span>{runStatus === 'idle' ? 'Visitor controlled' : `Run status / ${runStatus}`}</span>
      </div>

      <div className="cd-deck-system">
        <div className="cd-deck-core" aria-label="VibeAI manager coordinating specialist agents">
          <span className="cd-deck-core-orbit" aria-hidden="true" />
          <strong>V</strong>
          <small>MANAGER</small>
        </div>

        <ul className="cd-deck-agents" aria-label="Assigned specialist agents">
          {SPECIALIST_ROLES.map((role, index) => (
            <li className={`cd-agent cd-agent-${role.token}`} key={role.name}>
              <span className="cd-agent-index">0{index + 1}</span>
              <span>
                <strong>{role.name}</strong>
                <small>{role.detail}</small>
              </span>
              <i aria-hidden="true" />
            </li>
          ))}
        </ul>
      </div>

      <ol className="cd-deck-stages" aria-label="Orchestration stages">
        {TRACE_STAGES.map((stage, index) => {
          const isCurrent = activeStage === index
          const isComplete = runStatus === 'complete' || (activeStage !== null && index < activeStage)
          return (
            <li
              className={`${isCurrent ? 'is-current' : ''}${isComplete ? ' is-complete' : ''}`}
              key={stage.key}
              aria-current={isCurrent ? 'step' : undefined}
            >
              <button type="button" onClick={() => inspect(index)} aria-label={`Inspect ${stage.label} stage`}>
                <span className="cd-stage-index">{String(index + 1).padStart(2, '0')}</span>
                <span>
                  <strong>{stage.label}</strong>
                  <small>{stage.role}</small>
                  <em>{stage.detail}</em>
                </span>
              </button>
            </li>
          )
        })}
      </ol>

      <p className="cd-deck-live" role="status" aria-live="polite">{liveMessage}</p>

      <footer className={`cd-deck-verdict${runStatus === 'complete' ? ' is-complete' : ''}`}>
        <span className="cd-verdict-mark" aria-hidden="true">✓</span>
        <span>
          <small>VERIFIED OUTCOME</small>
          <strong>Specialists aligned. Checks complete.</strong>
        </span>
      </footer>
    </article>
  )
}
