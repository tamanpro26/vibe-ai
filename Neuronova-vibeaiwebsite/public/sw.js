// Service worker for the fire-alert Web Push notifications (school heat
// monitor). Kept deliberately minimal: this project has no offline/PWA
// ambitions elsewhere, so the only two events handled are the two a push
// notification actually needs -- receiving one, and reacting to a click.

self.addEventListener('push', (event) => {
  let title = 'VibeAI Alert'
  let body = ''
  try {
    const data = event.data ? event.data.json() : {}
    title = data.title || title
    body = data.body || ''
  } catch {
    body = event.data ? event.data.text() : ''
  }

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: '/favicon.svg',
      tag: 'vibeai-fire-alert',
      // Replaces a still-showing "high temperature" notification with the
      // "all clear" one rather than stacking both -- the reader should see
      // the CURRENT state, not an accumulating log.
      renotify: true,
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus()
      }
      if (self.clients.openWindow) return self.clients.openWindow('/')
    }),
  )
})
