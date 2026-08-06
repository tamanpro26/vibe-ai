import { useEffect, useRef, useState } from 'react'
import { checkLive, checkTeam, checkOmni, checkEdge } from './engine.js'

/* Polls all four real engine tiers in parallel, live. Shared by ChatApp and
 * ProjectWorkspace so both always agree on what's actually reachable instead
 * of running two independent probes against the same endpoints.
 *
 * engineRef mirrors the state synchronously (no waiting on a React
 * re-render), because a message sent in the first couple seconds after page
 * load can race ahead of the very first probe -- it's a real network round
 * trip, not instant. probeRef holds the in-flight probe's promise so a
 * caller can await it before trusting engineRef, closing that race.
 * Reproduced live: sending a message immediately on page load hit "no live
 * engine reachable" despite every tier being confirmed working seconds
 * later.
 */
export function useEngineProbe() {
  const [live, setLive] = useState(false)
  const [manager, setManager] = useState(false)
  const [omni, setOmni] = useState(false)
  const [edge, setEdge] = useState(false)
  const engineRef = useRef({ live: false, manager: false, omni: false, edge: false })
  const probeRef = useRef(null)

  // Probed in parallel: a down server costs a full timeout, and serially
  // that would quadruple the delay. Re-checked on an interval so starting a
  // local server (or the edge function / team proxy going live on deploy)
  // upgrades the app without a reload.
  useEffect(() => {
    let alive = true
    const probe = async () => {
      const [okLive, okManager, okOmni, okEdge] = await Promise.all([
        checkLive(),
        checkTeam(),
        checkOmni(),
        checkEdge(),
      ])
      engineRef.current = { live: okLive, manager: okManager, omni: okOmni, edge: okEdge }
      if (!alive) return
      setLive(okLive)
      setManager(okManager)
      setOmni(okOmni)
      setEdge(okEdge)
    }
    probeRef.current = probe()
    const id = setInterval(() => {
      probeRef.current = probe()
    }, 20000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  return { live, manager, omni, edge, setLive, setManager, setOmni, setEdge, engineRef, probeRef }
}
