// User settings: profile, personalization, appearance.
//
// Stored per user, mirroring store.js's one-bucket-per-user convention, so
// switching accounts in the same browser never leaks one person's custom
// instructions into another's conversations.
//
// The theme is deliberately ALSO written to a separate, user-independent key
// (`vibeai_theme`). The blocking script in index.html has to apply the theme
// before React (and therefore before we know who is logged in), so it needs a
// key it can read without a user id. Without this the app would paint the
// default theme first and correct itself after hydration -- a visible flash on
// every load for anyone not using the default.

const key = (userId) => `vibeai_settings_${userId}`

export const THEME_KEY = 'vibeai_theme'
export const THEMES = ['cockpit', 'studio']

// Caps exist because every character here is prepended to EVERY request as a
// system prompt. Left unbounded, a long profile silently eats the context
// window and the token budget on all of a user's messages, not just one.
// The server must enforce its own limit too -- this one is a UX affordance,
// not a security control, since anything client-side can be bypassed.
export const LIMITS = { about: 1500, style: 1500, name: 60, role: 60 }

export const DEFAULTS = {
  displayName: '',
  callMe: '',
  role: '',
  about: '',
  style: '',
  theme: 'cockpit',
  density: 'comfortable',
  textSize: 'm',
  motion: 'full',
  chatFont: 'sans',
}

export const CHAT_FONTS = [
  { id: 'sans', label: 'Sans' },
  { id: 'mono', label: 'Mono' },
  { id: 'serif', label: 'Serif' },
]

export function loadSettings(userId) {
  try {
    const raw = localStorage.getItem(key(userId))
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS }
  } catch {
    // Corrupt JSON or a privacy mode that blocks storage: fall back to
    // defaults rather than letting the whole settings screen fail to mount.
    return { ...DEFAULTS }
  }
}

export function saveSettings(userId, settings) {
  try {
    localStorage.setItem(key(userId), JSON.stringify(settings))
    localStorage.setItem(THEME_KEY, settings.theme)
  } catch {
    // Storage full or unavailable. The in-memory state is still correct for
    // this session, so failing silently is better than blocking the user.
  }
}

export function applyTheme(theme) {
  const safe = THEMES.includes(theme) ? theme : 'cockpit'
  document.documentElement.dataset.theme = safe
  return safe
}

export function applyAppearance({ theme, density, textSize, motion, chatFont }) {
  applyTheme(theme)
  const root = document.documentElement
  root.dataset.density = density || 'comfortable'
  root.dataset.textSize = textSize || 'm'
  root.dataset.motion = motion || 'full'
  root.dataset.chatFont = chatFont || 'sans'
}

/**
 * Builds the system prompt from the user's profile + instructions.
 *
 * Returns an empty string when nothing is filled in -- callers must not send
 * an empty system message, which some providers reject and which otherwise
 * wastes tokens on every single request for users who never opened settings.
 */
export function composeSystemPrompt(settings, projectInstructions = '', projectMemory = '') {
  if (!settings) return ''
  const parts = []

  const who = []
  if (settings.callMe) who.push(`Address the user as "${settings.callMe}".`)
  if (settings.role) who.push(`Their work/role: ${settings.role}.`)
  if (settings.about) who.push(`About them: ${settings.about}`)
  if (who.length) parts.push(who.join(' '))

  if (settings.style) parts.push(`Response preferences: ${settings.style}`)

  // Memory before instructions: memory is context (what this project has
  // established so far), instructions are directives. When the two conflict,
  // the explicit instruction the user wrote should win over an inference a
  // summarizer drew, and later text carries more weight for weaker models --
  // the same recency logic the failure taxonomy documents for constraints.
  // Labelled so the model can tell an auto-derived summary from a human's
  // standing order and weigh them accordingly.
  if (projectMemory) {
    parts.push(
      `What this project has established so far (auto-summarized from earlier chats; ` +
        `treat as context, not as instructions):\n${projectMemory}`,
    )
  }

  // Project instructions come LAST so they can specialise (or override) the
  // global voice for that project, rather than being drowned out by it.
  if (projectInstructions) parts.push(projectInstructions)

  return parts.join('\n\n')
}
