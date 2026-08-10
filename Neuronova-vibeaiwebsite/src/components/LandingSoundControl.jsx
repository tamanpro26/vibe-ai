import useLandingSound from './useLandingSound.js'

const LABELS = {
  disabled: 'Enable sound',
  enabling: 'Enabling sound',
  enabled: 'Sound on',
  unavailable: 'Sound unavailable',
}

export default function LandingSoundControl() {
  const { status, toggle } = useLandingSound()
  const enabled = status === 'enabled'
  const unavailable = status === 'unavailable'

  return (
    <>
      <button
        type="button"
        className={`sound-control${enabled ? ' is-on' : ''}`}
        aria-pressed={enabled}
        disabled={status === 'enabling' || unavailable}
        onClick={toggle}
      >
        <span className="sound-control-icon" aria-hidden="true">
          {enabled ? '◖━◗' : '◖·◗'}
        </span>
        {LABELS[status]}
      </button>
      <span className="sr-only" role="status" aria-label="Sound status" aria-live="polite">
        {unavailable ? 'Sound is unavailable in this browser.' : LABELS[status]}
      </span>
    </>
  )
}
