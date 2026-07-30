import { useState } from 'react'
import { SignIn, SignUp, ClerkLoading, ClerkLoaded } from '@clerk/clerk-react'

/*
 * Clerk's prebuilt <SignIn>/<SignUp> own the whole flow now (validation,
 * verification email, error states, session creation) -- replacing a
 * hand-rolled form that only ever hashed a password into localStorage.
 * `appearance` restyles Clerk's own DOM to the HUD identity rather than
 * wrapping it in a lookalike shell, so the real widget (and its built-in
 * accessibility/edge-case handling) is what's on screen.
 */
const clerkAppearance = {
  variables: {
    colorPrimary: '#2be8ff',
    colorBackground: 'transparent',
    colorInputBackground: 'rgba(43, 232, 255, 0.05)',
    colorInputText: '#d9f6fb',
    colorText: '#d9f6fb',
    colorTextSecondary: '#7a94a0',
    colorDanger: '#ff5f6d',
    fontFamily: 'var(--font-body)',
    borderRadius: '6px',
  },
  elements: {
    rootBox: 'auth-clerk-root',
    card: 'auth-clerk-card',
    header: 'auth-clerk-hide',
    footer: 'auth-clerk-hide',
    dividerRow: 'auth-clerk-divider',
    formFieldInput: 'auth-clerk-input',
    formButtonPrimary: 'auth-clerk-submit',
    socialButtonsBlockButton: 'auth-clerk-social',
  },
}

export default function AuthPage({ initialMode = 'login' }) {
  const [mode, setMode] = useState(initialMode)

  return (
    <div className="auth-page">
      <a className="auth-back" href="#/">
        ← back to site
      </a>
      <div className="auth-panel">
        <div className="auth-brand">
          <svg className="nav-mark" viewBox="0 0 64 64" aria-hidden="true">
            <g stroke="currentColor" strokeWidth="4" fill="none">
              <path d="M32 8 L53 20 L53 44 L32 56 L11 44 L11 20 Z" />
            </g>
            <circle cx="32" cy="32" r="6" fill="currentColor" />
          </svg>
          <span>
            VIBE<span className="nav-brand-accent">AI</span> CHAT
          </span>
        </div>
        <div className="auth-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'is-active' : ''}
            onClick={() => setMode('login')}
          >
            Log in
          </button>
          <button
            role="tab"
            aria-selected={mode === 'signup'}
            className={mode === 'signup' ? 'is-active' : ''}
            onClick={() => setMode('signup')}
          >
            Sign up
          </button>
        </div>
        {/*
         * useAuth()'s isLoaded (the ChatShell gate in App.jsx) only means
         * Clerk-the-library has resolved the session -- it does NOT mean
         * <SignIn>/<SignUp> have finished mounting and applying the
         * `appearance` restyle above. Without this gate, Clerk's own
         * default, unstyled, plain-white form flashes for a beat before
         * snapping into the cyan HUD version -- visually a totally
         * different-looking "login page" appearing in front of the real
         * one. ClerkLoading/ClerkLoaded track that finer-grained readiness.
         */}
        <ClerkLoading>
          <div className="auth-clerk-loading" aria-hidden="true">
            <span className="auth-clerk-spinner" />
          </div>
        </ClerkLoading>
        <ClerkLoaded>
          {mode === 'login' ? (
            <SignIn routing="virtual" appearance={clerkAppearance} />
          ) : (
            <SignUp routing="virtual" appearance={clerkAppearance} />
          )}
        </ClerkLoaded>
        <p className="auth-note">Secured by Clerk. Your credentials never touch VibeAI's servers.</p>
      </div>
    </div>
  )
}
