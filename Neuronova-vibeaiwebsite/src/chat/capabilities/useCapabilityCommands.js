import { useEffect, useState } from 'react'
import { capabilityApi } from './capabilityApi.js'
import { buildCapabilityCommands } from './commands.js'

export default function useCapabilityCommands() {
  const [commands, setCommands] = useState([])

  useEffect(() => {
    let cancelled = false
    capabilityApi.list()
      .then((result) => {
        if (!cancelled) setCommands(buildCapabilityCommands(result.items || []))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  return commands
}
