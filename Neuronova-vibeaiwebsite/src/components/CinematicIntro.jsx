import { useCallback, useEffect, useRef, useState } from 'react'

const INTRO_STORAGE_KEY = 'vibeai-cinematic-intro-seen'
const INTRO_DURATION_MS = 3100

export default function CinematicIntro({ motionMode, onFinished }) {
  const [visible, setVisible] = useState(false)
  const timerRef = useRef(0)

  const finish = useCallback(() => {
    window.clearTimeout(timerRef.current)
    try {
      window.sessionStorage.setItem(INTRO_STORAGE_KEY, '1')
    } catch {
      // The visual must never depend on storage being writable.
    }
    setVisible(false)
    onFinished()
  }, [onFinished])

  useEffect(() => {
    if (motionMode !== 'full') {
      finish()
      return undefined
    }

    let seen = false
    try {
      seen = window.sessionStorage.getItem(INTRO_STORAGE_KEY) === '1'
    } catch {
      // Storage can be unavailable in hardened browsing modes. The intro is
      // still safe to show because it is brief, non-modal, and skippable.
    }

    if (seen) {
      onFinished()
      return undefined
    }
    setVisible(true)
    timerRef.current = window.setTimeout(finish, INTRO_DURATION_MS)

    return () => window.clearTimeout(timerRef.current)
  }, [finish, motionMode, onFinished])

  if (!visible) return null

  return (
    <div
      className="cinematic-intro"
      style={{ '--cinematic-intro-duration': `${INTRO_DURATION_MS}ms` }}
      role="dialog"
      aria-label="VibeAI introduction"
      aria-modal="false"
    >
      <div className="cinematic-intro-noise" aria-hidden="true" />
      <svg className="cinematic-intro-routes" viewBox="0 0 1000 620" aria-hidden="true">
        <path d="M80 130 C260 130 320 310 500 310" />
        <path d="M920 130 C740 130 680 310 500 310" />
        <path d="M100 500 C280 500 330 310 500 310" />
        <path d="M900 500 C720 500 670 310 500 310" />
        <circle cx="80" cy="130" r="7" />
        <circle cx="920" cy="130" r="7" />
        <circle cx="100" cy="500" r="7" />
        <circle cx="900" cy="500" r="7" />
        <circle className="cinematic-intro-core" cx="500" cy="310" r="34" />
      </svg>

      <div className="cinematic-intro-copy">
        <span className="cinematic-intro-mark" aria-hidden="true">V</span>
        <p>MANAGER / SPECIALIST ORCHESTRATION</p>
        <h2>
          <span>One request.</span>
          <span>Many minds.</span>
        </h2>
        <div className="cinematic-intro-status" aria-hidden="true">
          <span>PLAN</span><i />
          <span>ROUTE</span><i />
          <span>VERIFY</span>
        </div>
      </div>

      <button type="button" className="cinematic-intro-skip" onClick={finish}>
        Skip introduction
      </button>
    </div>
  )
}
