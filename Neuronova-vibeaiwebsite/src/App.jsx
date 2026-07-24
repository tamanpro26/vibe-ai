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

function Landing() {
  useReveal()

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

function ChatShell() {
  const { user } = useAuth()
  return user ? <ChatApp /> : <AuthPage />
}

export default function App() {
  const hash = useHashRoute()
  const isChat = hash.startsWith('#/chat')

  return <AuthProvider>{isChat ? <ChatShell /> : <Landing />}</AuthProvider>
}
