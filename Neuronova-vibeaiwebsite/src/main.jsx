import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// GitHub OAuth redirect URIs cannot contain a fragment. Route the signed
// callback query into the hash-based Capability Hub without dropping it.
const startupQuery = new URLSearchParams(window.location.search)
if (
  startupQuery.get('capability_callback') === 'github'
  && startupQuery.get('state')
  && startupQuery.get('code')
  && !window.location.hash
) {
  window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#/capabilities`)
}

// A user can keep the app open while Vercel promotes a new build. If that
// tab later requests an old hashed lazy chunk, Vite raises preloadError;
// one reload moves the tab onto the current asset manifest instead of
// leaving Projects or Chat as a blank route.
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  window.location.reload()
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
