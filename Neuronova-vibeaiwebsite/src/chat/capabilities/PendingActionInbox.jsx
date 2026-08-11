import { useCallback, useEffect, useState } from 'react'
import ActionConfirmation from './ActionConfirmation.jsx'
import { actionApi } from './capabilityApi.js'
import './capability.css'

function sameActions(current, next) {
  return current.length === next.length && current.every((item, index) => (
    item.id === next[index]?.id
    && item.status === next[index]?.status
    && item.request_digest === next[index]?.request_digest
  ))
}

export default function PendingActionInbox() {
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState(null)
  const [stale, setStale] = useState(false)
  const refresh = useCallback(async () => {
    if (document.visibilityState === 'hidden') return
    try {
      const next = (await actionApi.pending()).items || []
      setItems((current) => sameActions(current, next) ? current : next)
      setStale(false)
    } catch { setStale(true) }
  }, [])
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000)
    const onVisibility = () => { if (document.visibilityState === 'visible') refresh() }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('vibeai:actions-changed', refresh)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('vibeai:actions-changed', refresh)
    }
  }, [refresh])
  if (items.length === 0) return null
  return (
    <>
      <button className="cap-inbox" onClick={() => setSelected(items[0])}>
        <span>{items.length}</span> action{items.length === 1 ? '' : 's'} waiting for review{stale ? ' · reconnecting' : ''}
      </button>
      {selected && <ActionConfirmation action={selected} onClose={() => setSelected(null)} onChanged={() => { setSelected(null); refresh() }} />}
    </>
  )
}
