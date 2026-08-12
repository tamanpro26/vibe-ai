import { useEffect, useState } from 'react'
import { loadSettings } from './settings.js'

export default function useSlashCommandsEnabled(userId) {
  const [enabled, setEnabled] = useState(() => loadSettings(userId).slashCommandsEnabled)

  useEffect(() => {
    setEnabled(loadSettings(userId).slashCommandsEnabled)
  }, [userId])

  const applySettings = (settings) => setEnabled(settings.slashCommandsEnabled)
  return [enabled, applySettings]
}
