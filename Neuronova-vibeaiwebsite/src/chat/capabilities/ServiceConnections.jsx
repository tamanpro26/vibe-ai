import { useEffect, useState } from 'react'
import { integrationRequest } from './capabilityApi.js'

export default function ServiceConnections() {
  const [items, setItems] = useState([])
  const [message, setMessage] = useState('')
  const refresh = async () => {
    try { setItems((await integrationRequest('list')).items || []) } catch (err) { setMessage(err.message) }
  }
  useEffect(() => {
    const search = new URLSearchParams(window.location.search)
    const hash = window.location.hash
    const hashQueryIndex = hash.indexOf('?')
    const hashSearch = new URLSearchParams(hashQueryIndex >= 0 ? hash.slice(hashQueryIndex + 1) : '')
    const state = search.get('state') || hashSearch.get('state')
    const installationId = search.get('installation_id') || hashSearch.get('installation_id')
    const code = search.get('code') || hashSearch.get('code')
    if (!state || (!installationId && !code)) {
      refresh()
      return
    }
    setMessage('Finishing GitHub connection…')
    integrationRequest('finish_github', {
      state,
      installation_id: installationId ? Number(installationId) : undefined,
      code: code || undefined,
    }).then((result) => {
      if (result?.url) {
        window.location.assign(result.url)
        return
      }
      const cleanHash = hashQueryIndex >= 0 ? hash.slice(0, hashQueryIndex) : hash
      const cleanUrl = `${window.location.pathname}${cleanHash || '#/capabilities'}`
      window.history.replaceState({}, '', cleanUrl)
      setMessage('GitHub connected.')
      refresh()
    }).catch((err) => setMessage(err.message))
  }, [])
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
