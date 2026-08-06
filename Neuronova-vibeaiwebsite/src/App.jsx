import { useEffect, useState } from 'react'
import Nav from './components/Nav.jsx'
import Hero from './components/Hero.jsx'
import Problem from './components/Problem.jsx'
import Failover from './components/Failover.jsx'
import Council from './components/Council.jsx'
import AgentLoop from './components/AgentLoop.jsx'
import Teams from './components/Teams.jsx'
import Engineering from './components/Engineering.jsx'
import Footer from './components/Footer.jsx'
import { AuthProvider, useAuth } from './chat/auth.jsx'
import AuthPage from './chat/AuthPage.jsx'
import ChatApp from './chat/ChatApp.jsx'
import ProjectsListPage from './chat/ProjectsListPage.jsx'
import ProjectWorkspace from './chat/ProjectWorkspace.jsx'
import useLenis from './useLenis.js'
import './App.css'
import './chat/chat.css'

function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onHash = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return hash
}

function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll('[data-reveal]')
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add('is-in')
            io.unobserve(e.target)
          }
        }
      },
      { threshold: 0.12 },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])
}

// Cursor-follow glow on `.spotlight-card` elements (team/CEO/stat cards):
// one delegated listener rather than one per card, since new cards can
// mount/unmount as sections reveal and a per-card effect would need to
// re-bind on every render. The glow itself is CSS (opacity on :hover) --
// this only ever writes the pointer's position, so it's inert whenever
// the pointer isn't over a card.
function useSpotlight() {
  useEffect(() => {
    function handleMove(e) {
      const card = e.target.closest?.('.spotlight-card')
      if (!card) return
      const rect = card.getBoundingClientRect()
      card.style.setProperty('--spot-x', `${((e.clientX - rect.left) / rect.width) * 100}%`)
      card.style.setProperty('--spot-y', `${((e.clientY - rect.top) / rect.height) * 100}%`)
    }
    window.addEventListener('mousemove', handleMove, { passive: true })
    return () => window.removeEventListener('mousemove', handleMove)
  }, [])
}

function Landing() {
  useReveal()
  useLenis()
  useSpotlight()

  return (
    <div className="app">
      <div className="bg-grid" aria-hidden="true" />
      <Nav />
      <main>
        <Hero />
        <Problem />
        <Failover />
        <Council />
        <AgentLoop />
        <Teams />
        <Engineering />
      </main>
      <Footer />
    </div>
  )
}

// Shared by every authed route (chat, projects): resolve the Clerk session
// once, show the same page either way, and gate on sign-in identically.
function Authed({ initialAuthMode, children }) {
  const { user, isLoaded } = useAuth()
  // Clerk resolves an existing session asynchronously; without this guard a
  // signed-in user would flash the login page for a frame on every reload.
  if (!isLoaded) return <div className="auth-loading" aria-hidden="true" />
  return user ? children : <AuthPage initialMode={initialAuthMode} />
}

export default function App() {
  const hash = useHashRoute()
  // Three project routes, matched longest-first so an earlier pattern can't
  // swallow a later segment:
  //   #/projects              -> the grid
  //   #/projects/<id>         -> that project's home (composer + Recents)
  //   #/projects/<id>/c/<cid> -> one chat inside that project
  const projectChatMatch = hash.match(/^#\/projects\/([^/]+)\/c\/([^/]+)/)
  const projectMatch = !projectChatMatch && hash.match(/^#\/projects\/([^/]+)/)
  const isProjects = !projectChatMatch && !projectMatch && hash.startsWith('#/projects')
  const isChat = !projectChatMatch && !projectMatch && !isProjects && hash.startsWith('#/chat')
  const initialAuthMode = hash.startsWith('#/chat/signup') ? 'signup' : 'login'

  let page = <Landing />
  if (projectChatMatch) {
    page = (
      <Authed initialAuthMode={initialAuthMode}>
        <ProjectWorkspace projectId={projectChatMatch[1]} chatId={projectChatMatch[2]} />
      </Authed>
    )
  } else if (projectMatch) {
    page = (
      <Authed initialAuthMode={initialAuthMode}>
        <ProjectWorkspace projectId={projectMatch[1]} chatId={null} />
      </Authed>
    )
  } else if (isProjects) {
    page = <Authed initialAuthMode={initialAuthMode}><ProjectsListPage /></Authed>
  } else if (isChat) {
    page = <Authed initialAuthMode={initialAuthMode}><ChatApp /></Authed>
  }

  return <AuthProvider>{page}</AuthProvider>
}
