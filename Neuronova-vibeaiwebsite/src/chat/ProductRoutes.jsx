import { lazy } from 'react'
import { AuthProvider, useAuth } from './auth.jsx'
import AuthPage from './AuthPage.jsx'
import './chat.css'

// Authentication is the only code every product route needs up front. The
// three signed-in surfaces have different dependency graphs, so defer each
// until Clerk confirms that surface will actually render.
const ChatApp = lazy(() => import('./ChatApp.jsx'))
const ProjectsListPage = lazy(() => import('./ProjectsListPage.jsx'))
const ProjectWorkspace = lazy(() => import('./ProjectWorkspace.jsx'))

// Shared by every product route: resolve the Clerk session once, show the
// same authentication surface, and gate chat/projects identically.
function Authed({ initialAuthMode, children }) {
  const { user, isLoaded } = useAuth()

  // Clerk resolves an existing session asynchronously. This guard prevents a
  // signed-in user from flashing the login page on each reload.
  if (!isLoaded) return <div className="auth-loading" aria-label="Loading your account" />
  return user ? children : <AuthPage initialMode={initialAuthMode} />
}

export default function ProductRoutes({ hash }) {
  // Match longest-first so a project URL cannot swallow its chat segment.
  //   #/projects              -> project grid
  //   #/projects/<id>         -> project home
  //   #/projects/<id>/c/<cid> -> project chat
  const projectChatMatch = hash.match(/^#\/projects\/([^/]+)\/c\/([^/]+)/)
  const projectMatch = !projectChatMatch && hash.match(/^#\/projects\/([^/]+)/)
  const isProjects = !projectChatMatch && !projectMatch && hash.startsWith('#/projects')
  const initialAuthMode = hash.startsWith('#/chat/signup') ? 'signup' : 'login'

  let page = <ChatApp />
  if (projectChatMatch) {
    page = <ProjectWorkspace projectId={projectChatMatch[1]} chatId={projectChatMatch[2]} />
  } else if (projectMatch) {
    page = <ProjectWorkspace projectId={projectMatch[1]} chatId={null} />
  } else if (isProjects) {
    page = <ProjectsListPage />
  }

  return (
    <AuthProvider>
      <Authed initialAuthMode={initialAuthMode}>{page}</Authed>
    </AuthProvider>
  )
}
