import { useEffect, useRef, useState } from 'react'

export default function CinematicField({ motionMode, cinematicReady }) {
  const mountRef = useRef(null)
  const [isPhone, setIsPhone] = useState(() => window.matchMedia('(max-width: 767px)').matches)

  useEffect(() => {
    const phoneQuery = window.matchMedia('(max-width: 767px)')
    const syncPhoneBreakpoint = () => setIsPhone(phoneQuery.matches)
    phoneQuery.addEventListener('change', syncPhoneBreakpoint)
    window.addEventListener('resize', syncPhoneBreakpoint, { passive: true })
    return () => {
      phoneQuery.removeEventListener('change', syncPhoneBreakpoint)
      window.removeEventListener('resize', syncPhoneBreakpoint)
    }
  }, [])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !cinematicReady || motionMode !== 'full') {
      return undefined
    }

    let disposed = false
    let animationFrame = 0
    let resizeObserver
    let visibilityObserver
    let traceObserver
    let runtime
    let isVisible = false
    let isLoading = false
    let removePointerListener
    const pointer = { x: 0, y: 0 }

    const stopRendering = () => {
      window.cancelAnimationFrame(animationFrame)
      animationFrame = 0
    }

    const render = () => {
      animationFrame = 0
      if (disposed) return
      if (isVisible && document.visibilityState === 'visible') {
        runtime?.render(pointer)
        animationFrame = window.requestAnimationFrame(render)
      }
    }

    const startRendering = () => {
      if (!animationFrame && runtime && isVisible) {
        animationFrame = window.requestAnimationFrame(render)
      }
    }

    const initialise = async () => {
      if (isPhone || !isVisible || isLoading || runtime || disposed) return
      isLoading = true
      try {
        const { default: createCinematicThreeField } = await import('./createCinematicThreeField.js')
        if (disposed || !isVisible) {
          isLoading = false
          return
        }

        runtime = createCinematicThreeField(mount)
        resizeObserver = new ResizeObserver(runtime.resize)
        resizeObserver.observe(mount)

        const onPointerMove = (event) => {
          pointer.x = (event.clientX / window.innerWidth - 0.5) * 2
          pointer.y = (event.clientY / window.innerHeight - 0.5) * 2
        }
        window.addEventListener('pointermove', onPointerMove, { passive: true })
        removePointerListener = () => window.removeEventListener('pointermove', onPointerMove)

        startRendering()
      } catch {
        resizeObserver?.disconnect()
        removePointerListener?.()
        runtime?.dispose()
        runtime = undefined
        isLoading = false
        // The CSS field remains as the no-WebGL fallback.
      }
    }

    const initialiseWhenIdle = () => {
      if (isPhone || !isVisible || runtime || isLoading) return
      const trace = document.querySelector('#command-deck')
      if (trace?.dataset.runStatus === 'running') {
        traceObserver?.disconnect()
        traceObserver = new MutationObserver(() => {
          if (trace.dataset.runStatus !== 'running' && isVisible) {
            traceObserver.disconnect()
            traceObserver = undefined
            initialise()
          }
        })
        traceObserver.observe(trace, { attributes: true, attributeFilter: ['data-run-status'] })
        return
      }
      initialise()
    }

    visibilityObserver = new IntersectionObserver(
      ([entry]) => {
        isVisible = entry.isIntersecting
        mount.dataset.fieldVisible = String(isVisible)
        if (isVisible) {
          if (runtime) startRendering()
          else initialiseWhenIdle()
        } else {
          traceObserver?.disconnect()
          traceObserver = undefined
          stopRendering()
        }
      },
      { rootMargin: '240px' },
    )
    visibilityObserver.observe(mount)

    const handleDocumentVisibility = () => {
      if (document.visibilityState === 'visible') startRendering()
      else stopRendering()
    }
    document.addEventListener('visibilitychange', handleDocumentVisibility)

    return () => {
      disposed = true
      stopRendering()
      resizeObserver?.disconnect()
      visibilityObserver?.disconnect()
      traceObserver?.disconnect()
      document.removeEventListener('visibilitychange', handleDocumentVisibility)
      removePointerListener?.()
      runtime?.dispose()
      delete mount.dataset.fieldVisible
    }
  }, [cinematicReady, isPhone, motionMode])

  return (
    <div
      className="cinematic-field"
      ref={mountRef}
      aria-hidden="true"
    >
      <span className="cinematic-field-ring cinematic-field-ring-a" />
      <span className="cinematic-field-ring cinematic-field-ring-b" />
      <span className="cinematic-field-core">V</span>
    </div>
  )
}
