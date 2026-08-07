import { useEffect, useRef, useState } from 'react'
import { motion, useInView, useReducedMotion, useScroll, useTransform } from 'motion/react'
import { OPS_LINES, WEBSITE_METRICS } from '../data.js'
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
    // Read from the token layer instead of hardcoding. Canvas can't consume CSS
    // variables directly, and the previous literals silently kept painting the
    // old red-orange accent after the palette moved to 2A's violet — a canvas
    // is the one surface a retheme can't reach on its own.
    const _css = getComputedStyle(document.documentElement)
    const ACCENT = _css.getPropertyValue('--accent').trim() || '#9184d9'
    const ACCENT_RGB = _css.getPropertyValue('--accent-rgb').trim() || '145, 132, 217'
    const AMBER = _css.getPropertyValue('--warning').trim() || '#d9a03c'
    const AMBER_RGB = '217, 160, 60'
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
            ? `rgba(${AMBER_RGB}, ${0.1 * fade})`
            : `rgba(${ACCENT_RGB}, ${0.13 * fade})`
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
        ctx.strokeStyle = `rgba(${ACCENT_RGB}, 0.5)`
        ctx.lineWidth = 1.4
        ctx.beginPath()
        ctx.moveTo(a.x + (b.x - a.x) * tail, a.y + (b.y - a.y) * tail)
        ctx.lineTo(x, y)
        ctx.stroke()
        ctx.fillStyle = ACCENT
        ctx.beginPath()
        ctx.arc(x, y, 1.9, 0, Math.PI * 2)
        ctx.fill()
      }

      for (const n of nodes) {
        const open = n.tripped > 0
        ctx.fillStyle = open ? AMBER : ACCENT
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
  const ref = useRef(null)
  const isInView = useInView(ref, { amount: 0.15 })
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!isInView || reduced) return undefined
    const id = setInterval(() => setCount((c) => c + 1), 1700)
    return () => clearInterval(id)
  }, [isInView, reduced])

  const visible = []
  const shown = Math.min(7, count)
  for (let i = count - shown; i < count; i++) {
    visible.push({ ...OPS_LINES[i % OPS_LINES.length], key: i })
  }

  return (
    <div className="ops-log" data-reveal style={{ transitionDelay: '0.35s' }} ref={ref}>
      <div className="ops-log-head">
        <span className="ops-log-title">ORCHESTRATOR FEED</span>
        <span className="ops-log-note">illustrative trace · real API is localhost-first</span>
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
  { n: WEBSITE_METRICS.registrySlots, label: 'model registry slots' },
  { n: WEBSITE_METRICS.providerRoutes, label: 'provider routes' },
  { n: WEBSITE_METRICS.collectedTests, label: 'collected tests' },
  { n: WEBSITE_METRICS.defaultBoundary, label: 'default API boundary' },
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
function AssemblingHeadline({ reduced }) {
  if (reduced) {
    return (
      <h1 className="hero-title" data-reveal>
        Complex work, <span className="hero-title-accent">orchestrated</span> across models.
      </h1>
    )
  }

  const lines = [
    { text: 'Complex work,', accent: false },
    { text: 'orchestrated', accent: true },
    { text: 'across models.', accent: false },
  ]

  return (
    <h1 className="hero-title" aria-label="Complex work, orchestrated across models.">
      {lines.map(({ text, accent }, i) => (
        <motion.span
          key={text}
          aria-hidden="true"
          className={accent ? 'hero-title-line hero-title-accent' : 'hero-title-line'}
          initial={{ opacity: 0, y: '0.38em', filter: 'blur(12px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
          transition={{
            duration: 0.8,
            delay: 0.08 + i * 0.1,
            ease: [0.16, 1, 0.3, 1],
          }}
        >
          {text}{i < lines.length - 1 ? ' ' : ''}
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
          VIBEAI / MULTI-PROVIDER ORCHESTRATION ENGINE
        </p>
        <AssemblingHeadline reduced={reduced} />
        <p className="hero-tagline" data-reveal style={{ transitionDelay: '0.16s' }}>
          Plan · Route · Execute · Critique · Verify
        </p>
        <p className="hero-sub" data-reveal style={{ transitionDelay: '0.24s' }}>
          VibeAI coordinates models, tools, memory, search, and coding workflows—then checks the
          result before it reports the work complete. It is free-first, local-first, and able to
          fail over to another provider when a route fails.
        </p>
        <div className="cta-row" data-reveal style={{ transitionDelay: '0.28s' }}>
          <a className="cta-primary" href="#/chat">
            Open the AI workspace
          </a>
          <a className="cta-secondary" href="#problem">
            Trace a request
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
