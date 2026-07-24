import { useEffect, useRef, useState } from 'react'
import { OPS_LINES } from '../data.js'

function NetworkCanvas() {
  const ref = useRef(null)

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas.getContext('2d')
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const COLORS = ['#2be8ff', '#2be8ff', '#2be8ff', '#7dffb0', '#ffb020', '#b98bff']
    let raf = 0
    let w = 0
    let h = 0
    let nodes = []

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = canvas.offsetWidth
      h = canvas.offsetHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const seed = () => {
      const count = Math.min(80, Math.floor((w * h) / 16000))
      nodes = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35,
        r: 1 + Math.random() * 1.6,
        c: COLORS[Math.floor(Math.random() * COLORS.length)],
      }))
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      const LINK = 140
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]
          const dx = a.x - b.x
          const dy = a.y - b.y
          const d = Math.hypot(dx, dy)
          if (d < LINK) {
            ctx.strokeStyle = `rgba(43, 232, 255, ${0.14 * (1 - d / LINK)})`
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(a.x, a.y)
            ctx.lineTo(b.x, b.y)
            ctx.stroke()
          }
        }
      }
      for (const n of nodes) {
        ctx.fillStyle = n.c
        ctx.globalAlpha = 0.75
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalAlpha = 1
      }
    }

    const tick = () => {
      for (const n of nodes) {
        n.x += n.vx
        n.y += n.vy
        if (n.x < -10) n.x = w + 10
        if (n.x > w + 10) n.x = -10
        if (n.y < -10) n.y = h + 10
        if (n.y > h + 10) n.y = -10
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
      draw()
    } else {
      raf = requestAnimationFrame(tick)
    }
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
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
        <span className="ops-log-note">simulated — real API binds to localhost by design</span>
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
  { n: '439', label: 'offline automated tests' },
  { n: '$0', label: 'paid API keys required' },
]

export default function Hero() {
  return (
    <section className="hero" id="top">
      <NetworkCanvas />
      <div className="hero-inner container">
        <p className="hero-eyebrow" data-reveal>
          SOLO-BUILT ENGINEERING PROJECT · PYTHON
        </p>
        <h1 className="hero-title" data-reveal style={{ transitionDelay: '0.08s' }}>
          VIBE<span className="hero-title-accent">AI</span>
        </h1>
        <p className="hero-tagline" data-reveal style={{ transitionDelay: '0.16s' }}>
          Multi-Provider Multi-Agent Orchestration Core
        </p>
        <p className="hero-sub" data-reveal style={{ transitionDelay: '0.24s' }}>
          An autonomous coding agent and multi-model reasoning pipeline built entirely on
          free-tier LLMs — with automatic cross-provider failover, structured critique, and a
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
              <span className="stat-n">{s.n}</span>
              <span className="stat-label">{s.label}</span>
            </div>
          ))}
        </div>
        <OpsLog />
      </div>
      <a className="hero-scroll" href="#problem" aria-label="Scroll to next section">
        <span className="hero-scroll-line" aria-hidden="true" />
        SCROLL
      </a>
    </section>
  )
}
