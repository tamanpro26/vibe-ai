// Conversation persistence — one localStorage bucket per user.

const key = (userId) => `vibeai_chats_${userId}`

export const loadChats = (userId) => JSON.parse(localStorage.getItem(key(userId)) || '[]')

export const saveChats = (userId, chats) => localStorage.setItem(key(userId), JSON.stringify(chats))

export const newId = () => crypto.randomUUID()

export function newConversation(team = 'auto') {
  return {
    id: newId(),
    title: 'New chat',
    team,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
  }
}

export function titleFrom(text) {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > 42 ? `${clean.slice(0, 42)}…` : clean || 'New chat'
}

// Sidebar grouping, ChatGPT-style: Today / Yesterday / Previous 7 days / Older
export function groupChats(chats) {
  const dayStart = (offset) => {
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    return d.getTime() - offset * 86400000
  }
  const today = dayStart(0)
  const yesterday = dayStart(1)
  const week = dayStart(7)
  const groups = { Today: [], Yesterday: [], 'Previous 7 days': [], Older: [] }
  for (const c of [...chats].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (c.updatedAt >= today) groups.Today.push(c)
    else if (c.updatedAt >= yesterday) groups.Yesterday.push(c)
    else if (c.updatedAt >= week) groups['Previous 7 days'].push(c)
    else groups.Older.push(c)
  }
  return Object.entries(groups).filter(([, list]) => list.length > 0)
}
