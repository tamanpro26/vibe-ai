import { useEffect, useRef, useState } from 'react'

function tone(context, frequency, start, duration, gain = 0.018) {
  const oscillator = context.createOscillator()
  const envelope = context.createGain()
  oscillator.type = 'sine'
  oscillator.frequency.setValueAtTime(frequency, start)
  envelope.gain.setValueAtTime(0.0001, start)
  envelope.gain.exponentialRampToValueAtTime(gain, start + 0.012)
  envelope.gain.exponentialRampToValueAtTime(0.0001, start + duration)
  oscillator.connect(envelope)
  envelope.connect(context.destination)
  oscillator.start(start)
  oscillator.stop(start + duration + 0.03)
}

export default function LandingSoundControl() {
  const contextRef = useRef(null)
  const [enabled, setEnabled] = useState(false)

  const getContext = async () => {
    if (!contextRef.current) {
      const AudioContext = window.AudioContext || window.webkitAudioContext
      if (!AudioContext) return null
      contextRef.current = new AudioContext()
    }
    await contextRef.current.resume()
    return contextRef.current
  }

  useEffect(() => {
    if (!enabled) return undefined
    const onClick = (event) => {
      if (!event.target.closest('a, button')) return
      const context = contextRef.current
      if (!context || context.state !== 'running') return
      tone(context, 420, context.currentTime, 0.055, 0.011)
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [enabled])

  const toggle = async () => {
    const context = await getContext()
    if (!context) return
    const next = !enabled
    setEnabled(next)
    if (next) {
      const start = context.currentTime + 0.02
      tone(context, 220, start, 0.11)
      tone(context, 330, start + 0.09, 0.12)
      tone(context, 495, start + 0.18, 0.18, 0.014)
    }
  }

  return (
    <button
      type="button"
      className={`sound-control${enabled ? ' is-on' : ''}`}
      aria-pressed={enabled}
      onClick={toggle}
    >
      <span className="sound-control-icon" aria-hidden="true">
        {enabled ? '◖◗' : '◖·◗'}
      </span>
      {enabled ? 'Sound on' : 'Enable sound'}
    </button>
  )
}
