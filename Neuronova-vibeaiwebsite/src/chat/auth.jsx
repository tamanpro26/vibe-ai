import { createContext, useContext } from 'react'
import { ClerkProvider, useUser, useClerk, useAuth as useClerkAuth } from '@clerk/clerk-react'

/*
 * Real auth via Clerk, replacing the old localStorage-only demo (accounts
 * lived only in one browser, no recovery, no cross-device session).
 *
 * useAuth() keeps the SAME shape the rest of the app already consumed
 * ({ user: { id, name, email } | null, logout }) so App.jsx, ChatApp.jsx and
 * Sidebar.jsx needed no changes -- only this file and AuthPage.jsx (which now
 * renders Clerk's own <SignIn>/<SignUp>) changed. `login`/`signup` are gone:
 * Clerk's prebuilt components own that flow directly.
 */

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

const AuthCtx = createContext(null)

function AuthBridge({ children }) {
  const { user: clerkUser, isLoaded } = useUser()
  const { signOut } = useClerk()
  // getToken() mints a short-lived session JWT on demand; this is what proves
  // to api/chat.js that a request came from a real logged-in browser, not an
  // anonymous script hitting our provider-key-holding endpoint directly.
  const { getToken } = useClerkAuth()

  const user = clerkUser
    ? {
        id: clerkUser.id,
        name: clerkUser.fullName || clerkUser.firstName || clerkUser.username || 'there',
        email: clerkUser.primaryEmailAddress?.emailAddress || '',
      }
    : null

  return (
    <AuthCtx.Provider value={{ user, isLoaded, logout: () => signOut(), getToken }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function AuthProvider({ children }) {
  if (!PUBLISHABLE_KEY) {
    throw new Error(
      'Missing VITE_CLERK_PUBLISHABLE_KEY. Add it to Neuronova-vibeaiwebsite/.env (get it from dashboard.clerk.com -> API Keys).',
    )
  }
  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="#/">
      <AuthBridge>{children}</AuthBridge>
    </ClerkProvider>
  )
}

export const useAuth = () => useContext(AuthCtx)
