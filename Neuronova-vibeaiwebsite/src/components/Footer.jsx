export default function Footer() {
  return (
    <footer className="footer" id="contact">
      <div className="container">
        <div className="footer-main" data-reveal>
          <p className="eyebrow">
            <span className="eyebrow-n">07</span> / CONTACT
          </p>
          <h2 className="footer-title">Talk to the system itself.</h2>
          <div className="cta-row">
            <a className="cta-primary" href="#/chat">
              Open VibeAI Chat
            </a>
            <a className="cta-secondary" href="mailto:tamanpro26@gmail.com">
              tamanpro26@gmail.com
            </a>
          </div>
          <p className="footer-name">
            Log in, chat with history, attach files, and get coding tasks back as a zip.
            <br />
            Built by Taman Roy Chowdhury
          </p>
        </div>
        <div className="footer-base">
          <span>VIBEAI / MULTI-PROVIDER MULTI-AGENT ORCHESTRATION CORE</span>
          <span>© {new Date().getFullYear()} Taman Roy Chowdhury · All rights reserved</span>
        </div>
      </div>
    </footer>
  )
}
