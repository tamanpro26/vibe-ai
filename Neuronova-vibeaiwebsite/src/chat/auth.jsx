import { createContext, useContext, useState } from 'react'

/*
 * Demo-only client-side auth. Users live in this browser's localStorage;
 * passwords are salted + SHA-256 hashed so nothing is stored in plain text.
 * Swap `signup`/`login` for calls to the real VibeAI FastAPI server when
 * the backend auth endpoints exist.
 */

const USERS_KEY = 'vibeai_users'
const SESSION_KEY = 'vibeai_session'

const loadUsers = () => JSON.parse(localStorage.getItem(USERS_KEY) || '[]')
const saveUsers = (users) => localStorage.setItem(USERS_KEY, JSON.stringify(users))

async function hashPassword(password, salt) {
  const data = new TextEncoder().encode(`${salt}:${password}`)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

function randomSalt() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function loadSession() {
  const s = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null')
  if (!s) return null
  const user = loadUsers().find((u) => u.id === s.userId)
  return user ? { id: user.id, name: user.name, email: user.email } : null
}

const AuthCtx = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(loadSession)

  const signup = async (name, email, password) => {
    const users = loadUsers()
    if (users.some((u) => u.email === email.toLowerCase())) {
      throw new Error('An account with this email already exists.')
    }
    const salt = randomSalt()
    const record = {
      id: crypto.randomUUID(),
      name: name.trim(),
      email: email.toLowerCase(),
      salt,
      hash: await hashPassword(password, salt),
      createdAt: Date.now(),
    }
    saveUsers([...users, record])
    localStorage.setItem(SESSION_KEY, JSON.stringify({ userId: record.id }))
    setUser({ id: record.id, name: record.name, email: record.email })
  }

  const login = async (email, password) => {
    const record = loadUsers().find((u) => u.email === email.toLowerCase())
    if (!record) throw new Error('No account found for this email.')
    const hash = await hashPassword(password, record.salt)
    if (hash !== record.hash) throw new Error('Incorrect password.')
    localStorage.setItem(SESSION_KEY, JSON.stringify({ userId: record.id }))
    setUser({ id: record.id, name: record.name, email: record.email })
  }

  const logout = () => {
    localStorage.removeItem(SESSION_KEY)
    setUser(null)
  }

  return <AuthCtx.Provider value={{ user, signup, login, logout }}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)
