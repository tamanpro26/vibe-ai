import { useEffect, useRef, useState } from 'react'

const SECTION_LINKS = [
  ['Why', '#problem'],
  ['Resilience', '#resilience'],
  ['Council', '#council'],
  ['Agent', '#agent'],
  ['Platform', '#platform'],
  ['Proof', '#engineering'],
  ['Control', '#security'],
]

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButtonRef = useRef(null)
  const menuRef = useRef(null)

  // Native scrolling keeps anchors, keyboard navigation, and browser history
  // predictable. rAF-gating prevents more than one state update per frame.
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

  useEffect(() => {
    if (!menuOpen) return undefined

    const firstLink = menuRef.current?.querySelector('a')
    firstLink?.focus()

    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      menuButtonRef.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

  const closeMenu = () => setMenuOpen(false)

  const followSectionLink = (href) => {
    closeMenu()
    requestAnimationFrame(() => {
      const destination = document.querySelector(href)
      if (!destination) return
      destination.setAttribute('tabindex', '-1')
      destination.focus({ preventScroll: true })
      destination.addEventListener('blur', () => destination.removeAttribute('tabindex'), { once: true })
    })
  }

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
        {SECTION_LINKS.map(([label, href]) => <a href={href} key={href}>{label}</a>)}
      </nav>
      <div className="nav-auth">
        <a className="nav-login" href="#/chat/login">
          Log in
        </a>
        <a className="nav-signup" href="#/chat/signup">
          <span className="status-dot" aria-hidden="true" />
          Sign up
        </a>
        <button
          className="nav-menu-button"
          type="button"
          aria-label={menuOpen ? 'Close section menu' : 'Open section menu'}
          aria-expanded={menuOpen}
          aria-controls="mobile-section-menu"
          onClick={() => setMenuOpen((open) => !open)}
          ref={menuButtonRef}
        >
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </button>
      </div>
      {menuOpen && (
        <nav className="nav-mobile-menu" id="mobile-section-menu" aria-label="Mobile sections" ref={menuRef}>
          <span className="nav-mobile-kicker">Explore the system</span>
          {SECTION_LINKS.map(([label, href], index) => (
            <a href={href} key={href} onClick={() => followSectionLink(href)}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              {label}
            </a>
          ))}
        </nav>
      )}
    </header>
  )
}
