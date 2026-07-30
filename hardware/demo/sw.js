// Service worker for the local fire-alert demo page (hardware/demo/index.html).
// Same push/notificationclick handling as the deployed site's sw.js, plus one
// addition: posts the payload back to any open page so the on-screen log can
// show it too -- useful for a demo where a judge is watching the screen, not
// just waiting for an OS notification banner.

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
    Promise.all([
      self.registration.showNotification(title, { body, tag: 'vibeai-fire-alert', renotify: true }),
      self.clients.matchAll({ type: 'window' }).then((clients) => {
        for (const client of clients) client.postMessage({ type: 'push-received', title, body })
      }),
    ]),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus()
      }
      if (self.clients.openWindow) return self.clients.openWindow('/demo')
    }),
  )
})
