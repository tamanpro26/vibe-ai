import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { fetchRegistry } from './engine.js'

/*
 * Registry — direction 2A's "02 · Model & team registry" screen.
 *
 * Every number and row here is read from the running backend
 * (GET /api/registry -> config/models_config.py::MODEL_REGISTRY). Nothing is
 * hardcoded, so the site cannot drift from the system it describes — which is
 * the failure this surface exists to prevent, and one this project has already
 * hit (a bundled provider list, and docs claiming teams that do not exist).
 */

function Stat({ label, value }) {
  return (
    <div className="reg-stat">
      <span className="reg-stat-value">{value}</span>
      <span className="reg-stat-label">{label}</span>
    </div>
  )
}

export default function RegistryPanel({ open, onClose }) {
  const reduceMotion = useReducedMotion()
  const dialogRef = useRef(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')

  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true)
    fetchRegistry().then((d) => {
      if (!alive) return
      setData(d)
      setLoading(false)
    })
    return () => {
      alive = false
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    dialogRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const entries = data?.entries

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (entries || []).filter((e) => {
      if (provider !== 'all' && e.provider !== provider) return false
      if (!q) return true
      return (
        e.model_id.toLowerCase().includes(q) ||
        e.api_model.toLowerCase().includes(q) ||
        (e.role || '').toLowerCase().includes(q) ||
        (e.team || '').toLowerCase().includes(q)
      )
    })
  }, [entries, query, provider])

  const fmtCtx = (n) => {
    if (!n) return '—'
    if (n >= 1_000_000) return `${Math.round(n / 1_000_000)}M`
    if (n >= 1000) return `${Math.round(n / 1000)}K`
    return String(n)
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="set-scrim"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14, ease: [0.22, 0.85, 0.28, 1] }}
        >
          <motion.div
            className="set-dialog is-single reg-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Model and team registry"
            tabIndex={-1}
            ref={dialogRef}
            onClick={(e) => e.stopPropagation()}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.2, ease: [0.22, 0.85, 0.28, 1] }}
          >
            <div className="set-pane reg-pane">
              <button className="set-close" onClick={onClose} aria-label="Close registry">
                ×
              </button>

              <h2 className="set-h2">Model &amp; team registry</h2>

              {loading && <p className="reg-status">Reading the roster from the backend…</p>}

              {!loading && !data?.ok && (
                <p className="reg-status reg-status-off" role="status">
                  The backend is not reachable, so the roster cannot be read. Rather than show a
                  bundled copy that might no longer match the running system, this shows nothing.
                </p>
              )}

              {!loading && data?.ok && (
                <>
                  <div className="reg-stats">
                    <Stat label="registry slots" value={data.count} />
                    <Stat label="distinct endpoints" value={data.distinct_endpoints} />
                    <Stat label="providers" value={data.provider_count} />
                  </div>
                  <p className="reg-note">
                    Slots outnumber endpoints because several slots deliberately point at the same
                    upstream model in different roles — a planner and a critic can be the same
                    weights doing different jobs.
                  </p>

                  <div className="reg-controls">
                    <input
                      className="set-input reg-search"
                      type="search"
                      placeholder="Filter by slot, model, role or team…"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                    <div className="reg-providers" role="group" aria-label="Filter by provider">
                      <button
                        type="button"
                        className={`reg-chip${provider === 'all' ? ' is-active' : ''}`}
                        onClick={() => setProvider('all')}
                      >
                        All {data.provider_count}
                      </button>
                      {(data.providers || []).map((p) => (
                        <button
                          key={p}
                          type="button"
                          className={`reg-chip${provider === p ? ' is-active' : ''}`}
                          onClick={() => setProvider(p)}
                        >
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="reg-table-wrap">
                    <table className="reg-table">
                      <thead>
                        <tr>
                          <th scope="col">Slot</th>
                          <th scope="col">Model id</th>
                          <th scope="col">Provider</th>
                          <th scope="col">Team</th>
                          <th scope="col">Role</th>
                          <th scope="col">Context</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((e) => (
                          <tr key={e.model_id}>
                            <td className="reg-mono">{e.model_id}</td>
                            <td className="reg-mono reg-dim">{e.api_model}</td>
                            <td>{e.provider}</td>
                            <td>{e.team || '—'}</td>
                            <td className="reg-dim">{e.role || '—'}</td>
                            <td className="reg-mono reg-num">{fmtCtx(e.context_window)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {rows.length === 0 && <p className="reg-status">No slot matches that filter.</p>}
                  </div>

                  <p className="reg-foot">
                    Showing {rows.length} of {data.count} registry slots.
                  </p>
                </>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
