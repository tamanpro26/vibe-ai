import { useEffect } from 'react'

export default function useCinematicScroll(motionMode, cinematicReady) {
  useEffect(() => {
    if (motionMode !== 'full' || !cinematicReady) return undefined

    const root = document.querySelector('.command-deck-landing')
    if (!root) return undefined

    let disposed = false
    let context

    async function initialise() {
      try {
        const [{ gsap }, { ScrollTrigger }] = await Promise.all([
          import('gsap'),
          import('gsap/ScrollTrigger'),
        ])
        if (disposed) return

        gsap.registerPlugin(ScrollTrigger)
        context = gsap.context(() => {
          gsap.to('.cd-hero-copy', {
            y: -72,
            opacity: 0.28,
            ease: 'none',
            scrollTrigger: { trigger: '.cd-hero', start: 'top top', end: 'bottom top', scrub: 0.8 },
          })
          gsap.to('.cd-deck', {
            y: 54,
            rotateX: -2.5,
            transformPerspective: 1200,
            ease: 'none',
            scrollTrigger: { trigger: '.cd-hero', start: 'top top', end: 'bottom top', scrub: 0.8 },
          })

          ScrollTrigger.create({
            start: 0,
            end: 'max',
            onUpdate: ({ progress }) => root.style.setProperty('--cinematic-progress', progress),
          })
        }, root)
      } catch {
        // Existing CSS motion remains the fallback if GSAP is unavailable.
      }
    }

    initialise()
    return () => {
      disposed = true
      context?.revert()
      root.style.removeProperty('--cinematic-progress')
    }
  }, [cinematicReady, motionMode])
}
