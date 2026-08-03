import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useAuth } from './auth.jsx'
import {
  CHAT_FONTS,
  LIMITS,
  THEMES,
  applyAppearance,
  loadSettings,
  saveSettings,
} from './settings.js'

const PANELS = [
  { id: 'profile', label: 'Profile' },
  { id: 'personalization', label: 'Personalization' },
  { id: 'appearance', label: 'Appearance' },
]

/* Wording taken from direction 2A's own Appearance panel. The previous copy
   ("Void black, signal cyan" / "Warm surfaces, generous radii") described the
   palette that 2A replaced, so it had stopped matching what the toggle does. */
const THEME_INFO = {
  cockpit: { name: 'Cockpit', desc: 'Dark ground, accent as line and glow.' },
  studio: { name: 'Studio', desc: 'Paper ground, same accent, same rules.' },
}

function Field({ label, hint, children }) {
  return (
    <label className="set-field">
      <span className="set-field-label">{label}</span>
      {hint && <span className="set-field-hint">{hint}</span>}
      {children}
    </label>
  )
}

export default function SettingsModal({ open, onClose }) {
  const { user } = useAuth()
  const reduceMotion = useReducedMotion()
  const [panel, setPanel] = useState('profile')
  const [settings, setSettings] = useState(() => loadSettings(user?.id))
  const dialogRef = useRef(null)

  // Re-read whenever the modal is opened rather than only on mount: settings
  // can be changed elsewhere (or the account switched) while this component
  // stays mounted, and reopening should never show stale values.
  useEffect(() => {
    if (open) setSettings(loadSettings(user?.id))
  }, [open, user?.id])

  // Persist and apply on every change. Appearance is applied live -- a theme
  // switch you have to confirm before seeing is a much worse way to choose a
  // theme than one that previews instantly.
  const update = (patch) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch }
      saveSettings(user?.id, next)
      applyAppearance(next)
      return next
    })
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    // Move focus into the dialog so keyboard and screen-reader users are not
    // left behind on the trigger button while a modal covers the page.
    dialogRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  /* AnimatePresence rather than `if (!open) return null`: without it the
     dialog is torn out of the DOM on the same frame it closes, so an exit
     animation can never play. This was the specific complaint — the panel
     simply appeared and simply vanished. */
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="set-scrim"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14, ease: [0.22, 0.85, 0.28, 1] }}
        >
          <motion.div
            className="set-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            tabIndex={-1}
            ref={dialogRef}
            onClick={(e) => e.stopPropagation()}
            /* Scales up from just under full size while rising a few pixels.
               Small numbers on purpose: a big scale reads as a cartoon zoom,
               and this needs to feel like a panel arriving, not a popup. */
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.2, ease: [0.22, 0.85, 0.28, 1] }}
          >
        <nav className="set-nav" aria-label="Settings sections">
          <p className="set-nav-title">Settings</p>
          {PANELS.map((p) => (
            <button
              key={p.id}
              className={`set-nav-item${panel === p.id ? ' is-active' : ''}`}
              onClick={() => setPanel(p.id)}
              aria-current={panel === p.id ? 'page' : undefined}
            >
              {p.label}
            </button>
          ))}
        </nav>

        <div className="set-pane">
          <button className="set-close" onClick={onClose} aria-label="Close settings">
            ×
          </button>

          {panel === 'profile' && (
            <section>
              <h2 className="set-h2">Profile</h2>
              <Field label="Full name">
                <input
                  className="set-input"
                  maxLength={LIMITS.name}
                  value={settings?.displayName || ''}
                  onChange={(e) => update({ displayName: e.target.value })}
                  placeholder={user?.name || ''}
                />
              </Field>
              <Field label="What should VibeAI call you?">
                <input
                  className="set-input"
                  maxLength={LIMITS.name}
                  value={settings?.callMe || ''}
                  onChange={(e) => update({ callMe: e.target.value })}
                />
              </Field>
              <Field label="What best describes your work?">
                <input
                  className="set-input"
                  maxLength={LIMITS.role}
                  value={settings?.role || ''}
                  onChange={(e) => update({ role: e.target.value })}
                  placeholder="Engineering, research, student…"
                />
              </Field>
              <Field label="Email">
                <input className="set-input" value={user?.email || ''} readOnly disabled />
              </Field>
            </section>
          )}

          {panel === 'personalization' && (
            <section>
              <h2 className="set-h2">Personalization</h2>
              <p className="set-note">
                Applied to every conversation. Kept short on purpose — this is prepended to each
                request, so length costs context on all of your messages.
              </p>
              <Field label="What should VibeAI know about you?">
                <textarea
                  className="set-textarea"
                  rows={5}
                  maxLength={LIMITS.about}
                  value={settings?.about || ''}
                  onChange={(e) => update({ about: e.target.value })}
                />
                <span className="set-count">
                  {(settings?.about || '').length}/{LIMITS.about}
                </span>
              </Field>
              <Field label="How should VibeAI respond?">
                <textarea
                  className="set-textarea"
                  rows={5}
                  maxLength={LIMITS.style}
                  value={settings?.style || ''}
                  onChange={(e) => update({ style: e.target.value })}
                />
                <span className="set-count">
                  {(settings?.style || '').length}/{LIMITS.style}
                </span>
              </Field>
            </section>
          )}

          {panel === 'appearance' && (
            <section>
              <h2 className="set-h2">Appearance</h2>
              <Field label="Theme">
                <div className="set-themes">
                  {THEMES.map((t) => (
                    <button
                      key={t}
                      className={`set-theme${settings.theme === t ? ' is-active' : ''}`}
                      onClick={() => update({ theme: t })}
                      aria-pressed={settings.theme === t}
                    >
                      <span className={`set-theme-swatch is-${t}`} aria-hidden="true" />
                      <span className="set-theme-name">{THEME_INFO[t].name}</span>
                      <span className="set-theme-desc">{THEME_INFO[t].desc}</span>
                    </button>
                  ))}
                </div>
              </Field>

              <Field label="Chat font" hint="Typeface used for message text.">
                <div className="set-seg">
                  {CHAT_FONTS.map((f) => (
                    <button
                      key={f.id}
                      className={`set-seg-btn${settings.chatFont === f.id ? ' is-active' : ''}`}
                      onClick={() => update({ chatFont: f.id })}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </Field>

              <Field label="Density">
                <div className="set-seg">
                  {['comfortable', 'compact'].map((d) => (
                    <button
                      key={d}
                      className={`set-seg-btn${settings.density === d ? ' is-active' : ''}`}
                      onClick={() => update({ density: d })}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </Field>

              <Field label="Text size">
                <div className="set-seg">
                  {['s', 'm', 'l'].map((s) => (
                    <button
                      key={s}
                      className={`set-seg-btn${settings.textSize === s ? ' is-active' : ''}`}
                      onClick={() => update({ textSize: s })}
                    >
                      {s.toUpperCase()}
                    </button>
                  ))}
                </div>
              </Field>

              <Field
                label="Motion"
                hint="Reduced and off are also forced automatically when your system asks for reduced motion."
              >
                <div className="set-seg">
                  {['full', 'reduced', 'off'].map((m) => (
                    <button
                      key={m}
                      className={`set-seg-btn${settings.motion === m ? ' is-active' : ''}`}
                      onClick={() => update({ motion: m })}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </Field>
            </section>
          )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
