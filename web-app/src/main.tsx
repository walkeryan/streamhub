import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router'
import './styles/base.css'
import { App } from './App'
import { startSpatialNavigation } from './nav/spatial'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
})

async function boot() {
  if (import.meta.env.VITE_MOCK === '1') {
    const { startMocks } = await import('./mocks/browser')
    await startMocks()
  }
  startSpatialNavigation()
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
