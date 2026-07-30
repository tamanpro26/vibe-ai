import { useEffect, useRef, useState } from 'react'
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react'
import { OPS_LINES } from '../data.js'
import CountUp from './CountUp.jsx'

/**
 * The hero's focal moment: the product animating its own behavior.
 *
 * This is not an ambient particle field. Signals are dispatched from an
 * orchestrator node and routed hop-by-hop across provider nodes, which is
 * literally what VibeAI does. Periodically one node "trips" (its circuit
 * opens, matching core/circuit_breaker), turns amber, and stops accepting
 * traffic; in-flight signals targeting it reroute to the nearest healthy
 * neighbour instead of dying. The failover section below states this in
 * words. Here the page demonstrates it before the copy makes the claim.
 */
function NetworkCanvas() {
  const ref = useRef(null)

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas.getContext('2d')
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const CYAN = '#2be8ff'
    const AMBER = '#ffb020'
    const LINK = 140

    let raf = 0
    let w = 0
    let h = 0
    let nodes = []
    let signals = []
    let lastSpawn = 0
    let lastTrip = 0

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = canvas.offsetWidth
      h = canvas.offsetHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const seed = () => {
      const count = Math.min(64, Math.floor((w * h) / 20000))
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.28,
        vy: (Math.random() - 0.5) * 0.28,
        r: 1 + Math.random() * 1.5,
        tripped: 0, // ms remaining on an open circuit
        pulse: 0, // 1 -> 0 flash when a signal lands
      }))
      signals = []
    }

    // Nearest healthy neighbour within link range. Returns -1 when the node is
    // isolated, which is what ends a signal's life naturally.
    const nextHop = (from, exclude) => {
      const a = nodes[from]
      let best = -1
      let bestD = LINK
      for (let i = 0; i < nodes.length; i++) {
        if (i === from || i === exclude || nodes[i].tripped > 0) continue
        const d = Math.hypot(a.x - nodes[i].x, a.y - nodes[i].y)
        if (d < bestD) {
          bestD = d
          best = i
        }
      }
      return best
    }

    const spawnSignal = () => {
      if (nodes.length < 4) return
      const from = Math.floor(Math.random() * nodes.length)
      if (nodes[from].tripped > 0) return
      const to = nextHop(from, -1)
      if (to === -1) return
      signals.push({ from, to, t: 0, hops: 0 })
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h)

      // Links first, so nodes and signals sit above the mesh.
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]
          const d = Math.hypot(a.x - b.x, a.y - b.y)
          if (d >= LINK) continue
          const fade = 1 - d / LINK
          const dead = a.tripped > 0 || b.tripped > 0
          ctx.strokeStyle = dead
            ? `rgba(255, 176, 32, ${0.1 * fade})`
            : `rgba(43, 232, 255, ${0.13 * fade})`
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(b.x, b.y)
          ctx.stroke()
        }
      }

      // Signals in flight.
      for (const s of signals) {
        const a = nodes[s.from]
        const b = nodes[s.to]
        if (!a || !b) continue
        const x = a.x + (b.x - a.x) * s.t
        const y = a.y + (b.y - a.y) * s.t
        // Short comet tail reading back toward the source.
        const tail = Math.max(0, s.t - 0.16)
        ctx.strokeStyle = 'rgba(43, 232, 255, 0.5)'
        ctx.lineWidth = 1.4
        ctx.beginPath()
        ctx.moveTo(a.x + (b.x - a.x) * tail, a.y + (b.y - a.y) * tail)
        ctx.lineTo(x, y)
        ctx.stroke()
        ctx.fillStyle = CYAN
        ctx.beginPath()
        ctx.arc(x, y, 1.9, 0, Math.PI * 2)
        ctx.fill()
      }

      for (const n of nodes) {
        const open = n.tripped > 0
        ctx.fillStyle = open ? AMBER : CYAN
        ctx.globalAlpha = open ? 0.9 : 0.6 + n.pulse * 0.4
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.r + n.pulse * 1.6, 0, Math.PI * 2)
        ctx.fill()
        // A tripped node carries a ring: the open circuit, visible at a glance.
        if (open) {
          ctx.globalAlpha = 0.5
          ctx.strokeStyle = AMBER
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2)
          ctx.stroke()
        }
        ctx.globalAlpha = 1
      }
    }

    const tick = (now) => {
      for (const n of nodes) {
        n.x += n.vx
        n.y += n.vy
        if (n.x < -10) n.x = w + 10
        if (n.x > w + 10) n.x = -10
        if (n.y < -10) n.y = h + 10
        if (n.y > h + 10) n.y = -10
        if (n.tripped > 0) n.tripped -= 16
        if (n.pulse > 0) n.pulse = Math.max(0, n.pulse - 0.045)
      }

      if (now - lastSpawn > 420) {
        lastSpawn = now
        spawnSignal()
      }
      // Open one circuit every ~5s so the reroute is legible but not constant.
      if (now - lastTrip > 5000 && nodes.length) {
        lastTrip = now
        const victim = Math.floor(Math.random() * nodes.length)
        nodes[victim].tripped = 2600
      }

      for (let i = signals.length - 1; i >= 0; i--) {
        const s = signals[i]
        s.t += 0.028
        // Mid-flight the destination trips: reroute rather than drop. This is
        // the whole thesis of the section below, shown instead of stated.
        if (nodes[s.to] && nodes[s.to].tripped > 0 && s.t < 0.9) {
          const alt = nextHop(s.from, s.to)
          if (alt !== -1) {
            s.to = alt
            s.t = Math.max(0, s.t - 0.1)
          }
        }
        if (s.t >= 1) {
          if (nodes[s.to]) nodes[s.to].pulse = 1
          s.hops += 1
          const next = nextHop(s.to, s.from)
          // Cap hops so a signal cannot loop the mesh forever.
          if (next !== -1 && s.hops < 4) {
            s.from = s.to
            s.to = next
            s.t = 0
          } else {
            signals.splice(i, 1)
          }
        }
      }

      draw()
      raf = requestAnimationFrame(tick)
    }

    const onResize = () => {
      resize()
      seed()
      if (reduced) draw()
    }

    resize()
    seed()
    if (reduced) {
      // Static mesh: the composition survives, the motion does not.
      draw()
    } else {
      raf = requestAnimationFrame(tick)
    }
    window.addEventListener('resize', onResize)

    // Stop the loop whenever the hero is scrolled away or the tab is hidden.
    // A canvas rAF running behind six other sections is pure battery cost with
    // nothing on screen to show for it. Two independent inputs, one derived
    // running state, so neither source can strand the other.
    let onScreen = true
    let pageVisible = !document.hidden
    let running = !reduced

    const sync = () => {
      if (reduced) return
      const next = onScreen && pageVisible
      if (next === running) return
      running = next
      if (running) {
        // Reset the cadence clocks so a long pause doesn't dump a burst of
        // spawns and trips the moment the hero scrolls back into view.
        lastSpawn = 0
        lastTrip = 0
        raf = requestAnimationFrame(tick)
      } else {
        cancelAnimationFrame(raf)
      }
    }

    const io = new IntersectionObserver(
      ([entry]) => {
        onScreen = entry.isIntersecting
        sync()
      },
      { threshold: 0 },
    )
    io.observe(canvas)

    const onVisibility = () => {
      pageVisible = !document.hidden
      sync()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('resize', onResize)
    }
  }, [])

  return <canvas ref={ref} className="hero-net" aria-hidden="true" />
}

function OpsLog() {
  const [count, setCount] = useState(3)

  useEffect(() => {
    const id = setInterval(() => setCount((c) => c + 1), 1700)
    return () => clearInterval(id)
  }, [])

  const visible = []
  const shown = Math.min(7, count)
  for (let i = count - shown; i < count; i++) {
    visible.push({ ...OPS_LINES[i % OPS_LINES.length], key: i })
  }

  return (
    <div className="ops-log" data-reveal style={{ transitionDelay: '0.35s' }}>
      <div className="ops-log-head">
        <span className="ops-log-title">ORCHESTRATOR FEED</span>
        <span className="ops-log-note">simulated. Real API binds to localhost by design.</span>
      </div>
      <div className="ops-log-body">
        {visible.map((line, i) => (
          <div className={`ops-line${i === visible.length - 1 ? ' is-new' : ''}`} key={line.key}>
            <span className="ops-tag" style={{ color: line.color }}>
              [{line.tag}]
            </span>
            <span className="ops-text">{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const STATS = [
  { n: '38', label: 'model registry entries' },
  { n: '10', label: 'free-tier providers' },
  { n: '475', label: 'offline automated tests' },
  { n: '$0', label: 'paid API keys required' },
]

/* The page's one focal moment: the wordmark assembles per character rather
 * than fading in as a block, so the first thing on screen resolves like a
 * system coming online instead of a slide transition.
 *
 * The characters are aria-hidden and the real word lives on the h1's
 * aria-label -- splitting text into per-glyph spans otherwise makes screen
 * readers announce it letter by letter ("V, I, B, E..."), and search engines
 * and copy/paste see fragments.
 */
function AssemblingWordmark({ reduced }) {
  const chars = [
    ...'VIBE'.split('').map((c) => ({ c, accent: false })),
    ...'AI'.split('').map((c) => ({ c, accent: true })),
  ]

  if (reduced) {
    return (
      <h1 className="hero-title" data-reveal style={{ transitionDelay: '0.08s' }}>
        VIBE<span className="hero-title-accent">AI</span>
      </h1>
    )
  }

  return (
    <h1 className="hero-title" aria-label="VibeAI">
      {chars.map(({ c, accent }, i) => (
        <motion.span
          key={i}
          aria-hidden="true"
          className={accent ? 'hero-title-accent hero-char' : 'hero-char'}
          initial={{ opacity: 0, y: '0.32em', filter: 'blur(14px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
          transition={{
            duration: 0.85,
            delay: 0.12 + i * 0.06,
            ease: [0.16, 1, 0.3, 1],
          }}
        >
          {c}
        </motion.span>
      ))}
    </h1>
  )
}

export default function Hero() {
  const sectionRef = useRef(null)
  const reduced = useReducedMotion()
  // Scroll-linked rather than time-linked: the hero's departure is tied to the
  // reader's own scroll position, so leaving the hero reads as a handoff into
  // the page instead of an element that merely scrolled past. Motion owns this
  // because continuous scroll values must never round-trip through React state.
  const { scrollYProgress } = useScroll({
    target: sectionRef,
    offset: ['start start', 'end start'],
  })
  const contentY = useTransform(scrollYProgress, [0, 1], [0, -60])
  const contentOpacity = useTransform(scrollYProgress, [0, 0.72], [1, 0])
  // The mesh recedes slower than the copy, so the two separate in depth as the
  // section leaves.
  const netOpacity = useTransform(scrollYProgress, [0, 0.9], [1, 0.15])

  return (
    <section className="hero" id="top" ref={sectionRef}>
      <motion.div
        className="hero-net-wrap"
        style={reduced ? undefined : { opacity: netOpacity }}
        aria-hidden="true"
      >
        <NetworkCanvas />
      </motion.div>
      <motion.div
        className="hero-inner container"
        style={reduced ? undefined : { y: contentY, opacity: contentOpacity }}
      >
        <p className="hero-eyebrow" data-reveal>
          SOLO-BUILT ENGINEERING PROJECT · PYTHON
        </p>
        <AssemblingWordmark reduced={reduced} />
        <p className="hero-tagline" data-reveal style={{ transitionDelay: '0.16s' }}>
          Multi-Provider Multi-Agent Orchestration Core
        </p>
        <p className="hero-sub" data-reveal style={{ transitionDelay: '0.24s' }}>
          An autonomous coding agent and multi-model reasoning pipeline built entirely on
          free-tier LLMs, with automatic cross-provider failover, structured critique, and a
          verifier battery that checks its own work before calling anything done.
        </p>
        <div className="cta-row" data-reveal style={{ transitionDelay: '0.28s' }}>
          <a className="cta-primary" href="#/chat">
            Open VibeAI Chat
          </a>
          <a className="cta-secondary" href="#problem">
            See how it works
          </a>
        </div>
        <div className="hero-stats" data-reveal style={{ transitionDelay: '0.3s' }}>
          {STATS.map((s) => (
            <div className="stat" key={s.label}>
              <span className="stat-n">
                <CountUp value={s.n} />
              </span>
              <span className="stat-label">{s.label}</span>
            </div>
          ))}
        </div>
        <OpsLog />
      </motion.div>
    </section>
  )
}
