import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import LandingSoundContext from './landingSoundContext.js'

const CUES = {
  consent: [
    [220, 0, 0.11, 0.018],
    [330, 0.09, 0.12, 0.018],
    [495, 0.18, 0.18, 0.014],
  ],
  start: [[260, 0, 0.09, 0.014], [390, 0.07, 0.12, 0.012]],
  advance: [[420, 0, 0.07, 0.011]],
  complete: [[520, 0, 0.08, 0.013], [780, 0.08, 0.15, 0.011]],
}

function tone(context, frequency, start, duration, gain) {
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

function playPattern(context, cue) {
  const start = context.currentTime + 0.02
  CUES[cue].forEach(([frequency, offset, duration, gain]) => {
    tone(context, frequency, start + offset, duration, gain)
  })
}

export function LandingSoundProvider({ children }) {
  const [status, setStatus] = useState('disabled')
  const contextRef = useRef(null)
  const enabledRef = useRef(false)
  const generationRef = useRef(0)
  const disposedRef = useRef(false)

  useEffect(
    () => () => {
      disposedRef.current = true
      enabledRef.current = false
      generationRef.current += 1
      const context = contextRef.current
      contextRef.current = null
      if (context && context.state !== 'closed') void context.close().catch(() => {})
    },
    [],
  )

  const playCue = useCallback((cue) => {
    const context = contextRef.current
    if (!enabledRef.current || !context || context.state !== 'running' || !CUES[cue]) return
    playPattern(context, cue)
  }, [])

  const toggle = useCallback(async () => {
    if (status === 'enabled') {
      generationRef.current += 1
      enabledRef.current = false
      setStatus('disabled')
      return
    }
    if (status !== 'disabled') return

    const AudioContext = window.AudioContext || window.webkitAudioContext
    if (!AudioContext) {
      setStatus('unavailable')
      return
    }

    const generation = generationRef.current + 1
    generationRef.current = generation
    setStatus('enabling')

    try {
      const context = contextRef.current || new AudioContext()
      contextRef.current = context
      await context.resume()
      if (disposedRef.current || generation !== generationRef.current) return
      enabledRef.current = true
      setStatus('enabled')
      playPattern(context, 'consent')
    } catch {
      if (!disposedRef.current && generation === generationRef.current) {
        enabledRef.current = false
        setStatus('unavailable')
      }
    }
  }, [status])

  const value = useMemo(() => ({ status, toggle, playCue }), [playCue, status, toggle])
  return <LandingSoundContext.Provider value={value}>{children}</LandingSoundContext.Provider>
}
