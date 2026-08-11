// Project persistence — one localStorage bucket per user, mirroring
// store.js's chat persistence exactly. Projects stay client-side the same
// way chats already do: consistent with the rest of this app, and no new
// server-side storage to build. That does mean a project doesn't follow you
// across devices/browsers, unlike claude.ai's server-side model -- real
// cross-device sync would need actual backend storage, a bigger change than
// this.

const key = (userId) => `vibeai_projects_${userId}`

/**
 * Migrate a project written by the single-thread version of this file.
 *
 * The original shape held ONE conversation as `project.messages`. A project
 * now holds MANY chats (`project.chats`), matching the reference's Recents
 * list. Old records are lifted in place on read rather than by a one-shot
 * migration script: there's no server to run a migration on, and a user who
 * hasn't opened the app since the change would otherwise silently lose the
 * conversation. Idempotent, so re-reading an already-migrated record is free.
 */
function migrate(project) {
  if (Array.isArray(project.chats)) {
    const { builds, ...rest } = project
    return { ...rest, latestBuild: project.latestBuild || builds?.[0] || null }
  }
  const legacy = Array.isArray(project.messages) ? project.messages : []
  // Both keys are intentionally dropped from the result: `messages` is
  // re-homed into chats[0] below, and the project-level memorySyncedCount
  // becomes a per-chat field.
  const {
    messages: _messages,
    memorySyncedCount: _syncedCount,
    builds: _builds,
    ...rest
  } = project
  return {
    ...rest,
    chats: legacy.length
      ? [
          {
            id: crypto.randomUUID(),
            title: 'Untitled',
            createdAt: project.createdAt || Date.now(),
            updatedAt: project.updatedAt || Date.now(),
            messages: legacy,
            // Carried over so an already-summarized conversation isn't
            // re-summarized (and re-billed) on first load after upgrading.
            memorySyncedCount: _syncedCount || 0,
          },
        ]
      : [],
    latestBuild: project.latestBuild || _builds?.[0] || null,
  }
}

export const loadProjects = (userId) => {
  try {
    const raw = JSON.parse(localStorage.getItem(key(userId)) || '[]')
    return Array.isArray(raw) ? raw.map(migrate) : []
  } catch {
    // Corrupt JSON shouldn't take down the whole Projects surface.
    return []
  }
}

/**
 * Returns { ok } or { ok: false, quota: true }.
 *
 * Unlike saveSettings (which swallows storage failures silently, correctly,
 * because losing a theme preference is survivable), this REPORTS failure.
 * Projects now hold uploaded file bodies, so hitting the ~5MB localStorage
 * ceiling is a realistic outcome rather than a theoretical one -- and
 * silently dropping a file the user just added, while the UI shows it
 * attached, would be the worst possible way to handle it. The caller is
 * expected to surface this.
 */
export function saveProjects(userId, projects) {
  try {
    localStorage.setItem(key(userId), JSON.stringify(projects))
    return { ok: true }
  } catch (err) {
    const quota =
      err instanceof DOMException &&
      (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED')
    return { ok: false, quota }
  }
}

export const newProjectId = () => crypto.randomUUID()

// Caps on uploaded reference files. localStorage gives roughly 5MB for the
// WHOLE origin -- shared with chat history and every other project -- so
// these are deliberately conservative. Without them one dropped log file
// would evict a user's entire chat history.
export const FILE_LIMITS = {
  perFileBytes: 128 * 1024, // 128 KB of text
  perProjectBytes: 1.5 * 1024 * 1024, // 1.5 MB across all of a project's files
  maxFiles: 40,
}

export function newProject(name, description) {
  const now = Date.now()
  return {
    id: newProjectId(),
    name: name.trim() || 'Untitled project',
    description: description.trim(),
    // User-written standing orders for this project.
    instructions: '',
    // Auto-summarized rolling context (engine.js::summarizeMemory), folded
    // into the system prompt alongside instructions. Project-level on
    // purpose: memory is what the PROJECT has established, so it carries
    // across every chat inside it. Per-chat memorySyncedCount tracks which
    // turns have already been folded in.
    memory: '',
    memoryUpdatedAt: null,
    starred: false,
    createdAt: now,
    updatedAt: now,
    chats: [],
    // Two origins, one list, distinguished by `source`:
    //   'agent'  — written by a real build (engine.js::buildProject)
    //   'upload' — added by the user as reference material
    // Both carry real content, so both can go in a downloaded zip and both
    // count against the size caps above.
    files: [],
    latestBuild: null,
  }
}

export function newProjectChat() {
  const now = Date.now()
  return {
    id: crypto.randomUUID(),
    title: 'Untitled',
    createdAt: now,
    updatedAt: now,
    messages: [],
    memorySyncedCount: 0,
  }
}

/** True when `text` looks like binary rather than source/text content. */
export function looksBinary(text) {
  // A NUL byte effectively never appears in text files and appears almost
  // immediately in real binaries, which makes it a cheap, reliable test --
  // far more so than trusting a file extension or the browser's MIME guess.
  return text.includes('\u0000')
}

export const fileBytes = (files) =>
  files.reduce((n, f) => n + new Blob([f.content]).size, 0)

export function mergeProjectFiles(existing, incoming) {
  const merged = [...existing]
  for (const file of incoming) {
    const index = merged.findIndex((current) => current.path === file.path)
    if (index >= 0) merged[index] = file
    else merged.push(file)
  }
  return merged
}

export const SORTS = [
  { value: 'updated', label: 'Last updated' },
  { value: 'created', label: 'Date created' },
  { value: 'name', label: 'Name' },
]

/**
 * Filter by free text, then sort. Starred projects always float to the top
 * regardless of sort -- a pin that a sort order could bury isn't a pin.
 */
export function visibleProjects(projects, query, sort) {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? projects.filter(
        (p) =>
          p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q),
      )
    : [...projects]

  filtered.sort((a, b) => {
    if (!!b.starred !== !!a.starred) return b.starred ? 1 : -1
    if (sort === 'name') return a.name.localeCompare(b.name)
    if (sort === 'created') return b.createdAt - a.createdAt
    return b.updatedAt - a.updatedAt
  })
  return filtered
}
