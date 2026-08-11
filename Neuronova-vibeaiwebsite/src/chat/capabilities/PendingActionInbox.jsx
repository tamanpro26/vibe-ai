import { useCallback, useEffect, useState } from 'react'
import ActionConfirmation from './ActionConfirmation.jsx'
import { actionApi } from './capabilityApi.js'

export default function PendingActionInbox() {
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState(null)
  const refresh = useCallback(async () => {
    try { setItems((await actionApi.pending()).items || []) } catch { setItems([]) }
  }, [])
  useEffect(() => { refresh(); const id = setInterval(refresh, 15000); return () => clearInterval(id) }, [refresh])
  if (items.length === 0) return null
  return (
    <>
      <button className="cap-inbox" onClick={() => setSelected(items[0])}>
        <span>{items.length}</span> action{items.length === 1 ? '' : 's'} waiting for review
      </button>
      {selected && <ActionConfirmation action={selected} onClose={() => setSelected(null)} onChanged={() => { setSelected(null); refresh() }} />}
    </>
  )
}
