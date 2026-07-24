import { useState } from 'react'
import { useAuth } from './auth.jsx'

export default function AuthPage() {
  const { login, signup } = useAuth()
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    if (!email.trim() || !password) {
      setError('Email and password are required.')
      return
    }
    if (mode === 'signup' && password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setBusy(true)
    try {
      if (mode === 'signup') await signup(name || email.split('@')[0], email, password)
      else await login(email, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

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
        <form className="auth-form" onSubmit={submit}>
          {mode === 'signup' && (
            <label>
              Name
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Taman"
                autoComplete="name"
              />
            </label>
          )}
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'signup' ? 'min. 8 characters' : '••••••••'}
              autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              required
            />
          </label>
          {error && <p className="auth-error">{error}</p>}
          <button className="auth-submit" type="submit" disabled={busy}>
            {busy ? 'Working…' : mode === 'signup' ? 'Create account' : 'Log in'}
          </button>
        </form>
        <p className="auth-note">
          Demo auth — accounts live only in this browser (salted &amp; hashed, never sent
          anywhere). Swaps for the real backend later.
        </p>
      </div>
    </div>
  )
}
