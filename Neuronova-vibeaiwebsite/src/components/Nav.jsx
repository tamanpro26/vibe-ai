export default function Nav() {
  return (
    <header className="nav">
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
      <a className="nav-status" href="#/chat">
        <span className="status-dot" aria-hidden="true" />
        LAUNCH CHAT
      </a>
    </header>
  )
}
