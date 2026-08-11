import { useEffect, useState } from 'react'
import { capabilityApi } from './capabilityApi.js'
import './capability.css'

export default function CapabilitySelect({ value, onChange }) {
  const [items, setItems] = useState([])
  useEffect(() => {
    let cancelled = false
    capabilityApi.list().then((result) => {
      if (!cancelled) setItems((result.items || []).filter((item) => item.installed))
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])
  if (items.length === 0) return null
  return <select className="cap-composer-select" aria-label="Use capability" value={value} onChange={(event) => onChange(event.target.value)}><option value="">Capabilities · Auto</option>{items.map((item) => <option value={item.manifest.capability_id} key={item.id}>{item.manifest.name}</option>)}</select>
}
