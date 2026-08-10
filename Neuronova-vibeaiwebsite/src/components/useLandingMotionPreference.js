import { useEffect, useState } from 'react'

function readMotionPreference(query) {
  const explicit = document.documentElement.dataset.motion
  if (explicit === 'off' || explicit === 'reduced') return explicit
  return query.matches ? 'reduced' : 'full'
}

export default function useLandingMotionPreference() {
  const [preference, setPreference] = useState(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    return readMotionPreference(query)
  })

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setPreference(readMotionPreference(query))
    const observer = new MutationObserver(update)

    query.addEventListener('change', update)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-motion'],
    })
    update()

    return () => {
      query.removeEventListener('change', update)
      observer.disconnect()
    }
  }, [])

  return preference
}
