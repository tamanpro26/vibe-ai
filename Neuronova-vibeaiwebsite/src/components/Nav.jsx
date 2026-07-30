import { useEffect, useState } from 'react'

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)

  // Lenis (useLenis.js) still drives the real document scroll position, so
  // a plain native 'scroll' listener sees it -- no need to hook Lenis's own
  // event. rAF-gated so this can't fire more than once per frame regardless
  // of how many scroll events land in between.
  useEffect(() => {
    let ticking = false
    const onScroll = () => {
      if (ticking) return
      ticking = true
      requestAnimationFrame(() => {
        setScrolled(window.scrollY > 40)
        ticking = false
      })
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header className={`nav${scrolled ? ' is-scrolled' : ''}`}>
      <a className="nav-brand" href="#top">
        <svg className="nav-mark" viewBox="0 0 64 64" aria-hidden="true">
          <g stroke="currentColor" strokeWidth="4" fill="none">
            <path d="M32 8 L53 20 L53 44 L32 56 L11 44 L11 20 Z" />
          </g>
          <circle cx="32" cy="32" r="6" fill="currentColor" />
        </svg>
        VIBE<span className="nav-brand-accent">AI</span>
      </a>
      <nav className="nav-links" aria-label="Sections">
        <a href="#problem">Why</a>
        <a href="#resilience">Resilience</a>
        <a href="#council">Council</a>
        <a href="#agent">Agent</a>
        <a href="#teams">Teams</a>
        <a href="#engineering">Numbers</a>
        <a href="#contact">Contact</a>
      </nav>
      <div className="nav-auth">
        <a className="nav-login" href="#/chat/login">
          Log in
        </a>
        <a className="nav-signup" href="#/chat/signup">
          <span className="status-dot" aria-hidden="true" />
          Sign up
        </a>
      </div>
    </header>
  )
}
