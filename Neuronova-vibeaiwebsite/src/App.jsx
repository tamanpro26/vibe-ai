import { Component, lazy, Suspense, useEffect, useState } from 'react'
import { MotionConfig } from 'motion/react'
import Nav from './components/Nav.jsx'
import Hero from './components/Hero.jsx'
import LandingSoundControl from './components/LandingSoundControl.jsx'
import { LandingSoundProvider } from './components/LandingSoundContext.jsx'
import useLandingMotionPreference from './components/useLandingMotionPreference.js'
import PlatformShowcase from './components/PlatformShowcase.jsx'
import Problem from './components/Problem.jsx'
import Failover from './components/Failover.jsx'
import Council from './components/Council.jsx'
import AgentLoop from './components/AgentLoop.jsx'
import Teams from './components/Teams.jsx'
import Ecosystem from './components/Ecosystem.jsx'
import Engineering from './components/Engineering.jsx'
import Security from './components/Security.jsx'
import Footer from './components/Footer.jsx'
import './App.css'
import './landing-motion.css'
import './command-deck.css'

// The public page is the first experience for most visitors. Clerk, JSZip,
// the chat engine, project workspace, and their 3,000+ lines of CSS are only
// useful after a product route is opened, so keep them behind one route-level
// boundary instead of charging every landing-page visit for the full app.
const ProductRoutes = lazy(() => import('./chat/ProductRoutes.jsx'))

class ProductRouteBoundary extends Component {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main className="route-loading route-error" role="alert">
        <span className="route-loading-mark" aria-hidden="true" />
        <h1>The workspace could not load.</h1>
        <p>A network interruption or a newer deployment may have replaced this route.</p>
        <div className="route-error-actions">
          <button type="button" className="cta-primary" onClick={() => window.location.reload()}>
            Reload workspace
          </button>
          <a className="cta-secondary" href="#/">
            Back to site
          </a>
        </div>
      </main>
    )
  }
}

function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onHash = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return hash
}

function useReveal(motionMode) {
  useEffect(() => {
    const els = document.querySelectorAll('[data-reveal]')
    if (motionMode !== 'full') {
      els.forEach((el) => el.classList.add('is-in'))
      return undefined
    }
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
  }, [motionMode])
}

// Cursor-follow glow on `.spotlight-card` elements (team/CEO/stat cards):
// one delegated listener rather than one per card, since new cards can
// mount/unmount as sections reveal and a per-card effect would need to
// re-bind on every render. The glow itself is CSS (opacity on :hover) --
// this only ever writes the pointer's position, so it's inert whenever
// the pointer isn't over a card.
function useSpotlight(motionMode) {
  useEffect(() => {
    if (motionMode !== 'full') return undefined
    function handleMove(e) {
      const card = e.target.closest?.('.spotlight-card')
      if (!card) return
      const rect = card.getBoundingClientRect()
      card.style.setProperty('--spot-x', `${((e.clientX - rect.left) / rect.width) * 100}%`)
      card.style.setProperty('--spot-y', `${((e.clientY - rect.top) / rect.height) * 100}%`)
    }
    window.addEventListener('mousemove', handleMove, { passive: true })
    return () => window.removeEventListener('mousemove', handleMove)
  }, [motionMode])
}

function Landing() {
  const motionMode = useLandingMotionPreference()
  useReveal(motionMode)
  useSpotlight(motionMode)

  return (
    <LandingSoundProvider>
      <MotionConfig reducedMotion={motionMode === 'full' ? 'never' : 'always'}>
        <div className="app command-deck-landing" data-effective-motion={motionMode}>
          <div className="bg-grid" aria-hidden="true" />
          <div className="landing-scanfield" aria-hidden="true" />
          <Nav />
          <main>
            <Hero motionMode={motionMode} />
            <PlatformShowcase />
            <Problem />
            <Failover />
            <Council />
            <AgentLoop />
            <Teams />
            <Ecosystem />
            <Engineering />
            <Security />
          </main>
          <Footer />
          <LandingSoundControl />
        </div>
      </MotionConfig>
    </LandingSoundProvider>
  )
}

export default function App() {
  const hash = useHashRoute()
  const isProductRoute = hash.startsWith('#/chat') || hash.startsWith('#/projects')

  if (!isProductRoute) return <Landing />

  return (
    <ProductRouteBoundary>
      <Suspense
      fallback={
        <main className="route-loading" aria-busy="true" aria-live="polite">
          <span className="route-loading-mark" aria-hidden="true" />
          <p>Opening the VibeAI workspace…</p>
        </main>
      }
    >
        <ProductRoutes hash={hash} />
      </Suspense>
    </ProductRouteBoundary>
  )
}
