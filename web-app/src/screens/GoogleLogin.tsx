import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, ErrorState } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'
import type { AuthTokens } from '../api/types'
import { ProfilePicker } from './Gate'

// React StrictMode remounts effects: consume the one-time handoff only once per page load.
let completion: Promise<AuthTokens> | undefined
function clearOAuthMarkers() {
  const url = new URL(window.location.href)
  url.searchParams.delete('google_complete')
  url.searchParams.delete('google_error')
  window.history.replaceState(null, '', url.pathname + url.search)
}
function completeLogin() {
  completion ??= fetch('/api/v1/auth/google/complete', { method: 'POST', credentials: 'same-origin' })
    .then(async (r) => {
      if (!r.ok) throw new Error('Sign-in expired or unauthorized. Please try again.')
      return await r.json() as AuthTokens
    })
  return completion
}

export function LoginGate() {
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const config = useQuery({ queryKey: ['auth-config'], queryFn: async () => {
    const r = await fetch('/api/v1/auth/config')
    if (!r.ok) throw new Error('Cannot load sign-in settings. Please retry.')
    return await r.json() as { google_enabled: boolean; google_required: boolean }
  } })
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.has('google_error')) {
      setError('Google sign-in was canceled, expired, or this account is not authorized.')
      clearOAuthMarkers()
    }
    if (!params.has('google_complete')) return
    setBusy(true)
    void completeLogin().then((t) => {
      clearOAuthMarkers()
      setSession(t.access_token, t.refresh_token, t.profile)
      qc.clear()
    }).catch((e: unknown) => {
      clearOAuthMarkers()
      setError(e instanceof Error ? e.message : 'Sign-in failed.')
    }).finally(() => setBusy(false))
  }, [qc, setSession])
  useEffect(() => {
    if (!config.data?.google_enabled) return
    const timer = window.setTimeout(() => void setFocus('google:signin'), 50)
    return () => window.clearTimeout(timer)
  }, [config.data])
  if (config.isPending) return <main className="p-8"><p>Loading sign-in…</p></main>
  if (config.error) return <main className="p-8"><ErrorState error={config.error} onRetry={() => void config.refetch()} /></main>
  if (!config.data.google_required && !config.data.google_enabled) return <ProfilePicker />
  return <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }}>
    <FocusGroup focusKey="google-login" className="flex flex-col items-center gap-6">
      <h1 className="display">Welcome to StreamHub</h1>
      <p>Sign in with your authorized Google account to watch or pair your TV.</p>
      {config.data.google_enabled
        ? <Button primary focusKey="google:signin" disabled={busy} onEnter={() => {
          const code = new URLSearchParams(window.location.search).get('code')?.toUpperCase() ?? ''
          const returnTo = window.location.pathname === '/pair'
            ? '/pair' + (/^[A-Z0-9]{6}$/.test(code) ? `?code=${code}` : '') : '/'
          window.location.assign(`/api/v1/auth/google/start?return_to=${encodeURIComponent(returnTo)}`)
        }}>{busy ? 'Completing sign-in…' : 'Continue with Google'}</Button>
        : <p role="alert">Google sign-in is not configured yet. Contact the server owner.</p>}
      {error && <p role="alert">{error}</p>}
      {!config.data.google_required && <ProfilePicker />}
    </FocusGroup>
  </main>
}
