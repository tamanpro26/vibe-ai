import { useEffect } from 'react'
import Lenis from 'lenis'

/**
 * Inertial scrolling for the landing route.
 *
 * Scoped deliberately: this hook is called from <Landing> only, never from the
 * chat shell. The chat app is a fixed 100dvh two-pane layout whose panes own
 * their own overflow; a window-level scroll hijacker there would fight those
 * containers. Because <Landing> unmounts on the #/chat route, the cleanup below
 * destroys the instance and native scrolling returns.
 */
export default function useLenis() {
  useEffect(() => {
    // Honor the OS setting: no hijacked scroll, no rAF loop. index.css restores
    // native `scroll-behavior: smooth` under the same query, so in-page anchors
    // still animate, just without the inertia layer.
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduced.matches) return

    const lenis = new Lenis({
      // ~1.1s to settle. Long enough to read as weight, short enough that it
      // never feels like input lag on a short marketing page.
      duration: 1.1,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)), // exponential ease-out
      // Touch devices already have native inertia tuned by the OS. Overriding it
      // is the usual reason "smooth scroll" libraries feel broken on phones.
      smoothWheel: true,
      syncTouch: false,
    })

    let raf = 0
    const loop = (time) => {
      lenis.raf(time)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)

    // In-page anchors must be handed to Lenis. Left native they jump instantly
    // while Lenis still believes it owns the scroll position, which desyncs the
    // two and strands the page mid-section.
    const onClick = (e) => {
      const link = e.target.closest('a[href^="#"]')
      if (!link) return
      const href = link.getAttribute('href')
      // Route links (#/chat) are navigation, not in-page targets.
      if (!href || href === '#' || href.startsWith('#/')) return
      const target = document.querySelector(href)
      if (!target) return
      e.preventDefault()
      lenis.scrollTo(target, { offset: -70 }) // clears the fixed nav
      history.pushState(null, '', href)
    }
    document.addEventListener('click', onClick)

    return () => {
      document.removeEventListener('click', onClick)
      cancelAnimationFrame(raf)
      lenis.destroy()
    }
  }, [])
}
