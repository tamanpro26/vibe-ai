import { useEffect, useState } from 'react'
import { integrationRequest } from './capabilityApi.js'

export default function ServiceConnections() {
  const [items, setItems] = useState([])
  const [message, setMessage] = useState('')
  const refresh = async () => {
    try { setItems((await integrationRequest('list')).items || []) } catch (err) { setMessage(err.message) }
  }
  useEffect(() => { refresh() }, [])
  const connect = async () => {
    try {
      const result = await integrationRequest('connect_github')
      window.location.assign(result.url)
    } catch (err) { setMessage(err.message) }
  }
  return (
    <section className="cap-connections">
      <div><h2>Service connections</h2><p>Least-privilege credentials stay encrypted behind the action broker.</p></div>
      <button className="cap-secondary" onClick={connect}>Connect GitHub App</button>
      {items.map((item) => <div className="cap-connection" key={item.id}><strong>GitHub</strong><span>{item.immutable_targets.length} approved repositories</span><span>{item.revoked ? 'Revoked' : 'Connected'}</span></div>)}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
