// Web Push subscribe flow for the school heat-monitor's fire alerts.
//
// This talks to the REAL backend directly (not the Vercel proxies used
// elsewhere in this app) -- deliberately, since subscribing to a safety
// alert is a public opt-in, not an authenticated chat action, and gating it
// behind Clerk login would be a real usability regression for the one
// thing that most wants zero friction. The backend's CORS config allows
// this site's origin specifically for that reason.
const BACKEND_URL = import.meta.env.VITE_VIBE_BACKEND_URL || ''

export function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && !!BACKEND_URL
}

// VAPID keys arrive base64url-encoded; PushManager.subscribe wants a raw
// Uint8Array. This is the standard conversion (base64url -> base64 -> bytes).
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

export async function subscribeToPushAlerts() {
  if (!pushSupported()) throw new Error('push not supported in this browser')

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('notification permission denied')

  const registration = await navigator.serviceWorker.register('/sw.js')
  await navigator.serviceWorker.ready

  const { publicKey } = await fetch(`${BACKEND_URL}/api/push/vapid-public-key`).then((r) => r.json())

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  })

  const res = await fetch(`${BACKEND_URL}/api/push/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subscription: subscription.toJSON() }),
  })
  if (!res.ok) throw new Error(`subscribe failed: ${res.status}`)
  return true
}

export async function pushSubscriptionStatus() {
  if (!pushSupported()) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  const reg = await navigator.serviceWorker.getRegistration('/sw.js')
  if (!reg) return 'unsubscribed'
  const sub = await reg.pushManager.getSubscription()
  return sub ? 'subscribed' : 'unsubscribed'
}
