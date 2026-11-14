import './lib/zodConfig'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router'
import './styles/base.css'
import { App } from './App'
import { queryClient } from './api/queryClient'
import { startSpatialNavigation } from './nav/spatial'
import { startGamepadNavigation } from './nav/gamepad'
import { installShell } from './shell'

async function boot() {
  // Inside the Android TV app: its viewport, Back and token bridge. Nothing at all in a browser.
  installShell()
  if (import.meta.env.VITE_MOCK === '1') {
    const { startMocks } = await import('./mocks/browser')
    await startMocks()
  }
  startSpatialNavigation()
  startGamepadNavigation()
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
}
void boot()
