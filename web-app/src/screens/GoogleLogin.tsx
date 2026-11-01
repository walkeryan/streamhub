import { useEffect, useRef, useState } from 'react'
import { useBackHandler } from '../nav/back'
import { useNavigate } from 'react-router'
import { OwnerPin } from '../components/OwnerPin'
import { OwnerPinSetup } from '../components/OwnerPinSetup'
import { isAuthTokens, type PendingOwnerLogin, type OwnerPinResult } from '../api/ownerPin'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, ErrorState } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'
import type { AuthTokens } from '../api/types'
import { ProfilePicker } from './Gate'

// React StrictMode remounts effects: consume the one-time handoff only once per page load.
let completion: Promise<AuthTokens | PendingOwnerLogin> | undefined
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
      return await r.json() as AuthTokens | PendingOwnerLogin
    })
  return completion
}

export function LoginGate() {
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [pending,setPending]=useState<PendingOwnerLogin|null>(null)
  const [setup,setSetup]=useState(false)
  const origin=useRef(0)
  const committing=useRef(false)
  const [finishing,setFinishing]=useState(false)
  const [error, setError] = useState<string | null>(()=>new URLSearchParams(window.location.search).has('owner_pin_uncertain')?'Authorization outcome is unknown. Sign in with Google and your owner PIN again. The previous submission will not be retried.':null)
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
      if ('owner_pin_required' in t && t.owner_pin_required && typeof t.pending_token === 'string' && t.profile?.id) {
        origin.current=useSession.getState().generation;setSetup(!!t.setup_required);setPending(t)
      } else if (isAuthTokens(t as OwnerPinResult)) {
        const tokens=t as AuthTokens;clearOAuthMarkers();setSession(tokens.access_token,tokens.refresh_token,tokens.profile);qc.clear()
      } else throw new Error('Sign-in did not return a valid owner authorization.')
    }).catch((e: unknown) => {
      clearOAuthMarkers()
      setError(e instanceof Error ? e.message : 'Sign-in failed.')
    }).finally(() => setBusy(false))
  }, [qc, setSession])
  useEffect(() => {
    if (!config.data?.google_enabled || pending) return
    const timer = window.setTimeout(() => void setFocus('google:signin'), 50)
    return () => window.clearTimeout(timer)
  }, [config.data,pending])
  useEffect(()=>{if(!pending)return;const timer=window.setTimeout(()=>setFocus('owner-login:cancel'),20);return()=>window.clearTimeout(timer)},[pending])
  const finishOwner = async(result:OwnerPinResult,signal:AbortSignal) => {
    if(signal.aborted)return
    if(!pending || useSession.getState().generation!==origin.current)throw new Error('Sign-in changed. Cancel and sign in again.')
    if(!isAuthTokens(result)||result.profile.id!==pending.profile.id)throw new Error('Owner authorization returned a different profile or action.')
    await qc.cancelQueries()
    if(signal.aborted || useSession.getState().generation!==origin.current)return
    clearOAuthMarkers();qc.clear();setSession(result.access_token,result.refresh_token,result.profile);setPending(null)
  }
  const cancelOwner = () => {if(committing.current)return;setPending(null);completion=undefined;clearOAuthMarkers();navigate(window.location.pathname+window.location.search,{replace:true})}
  const critical = (value:boolean) => {committing.current=value;setFinishing(value)}
  const uncertain = () => {
    if(useSession.getState().generation===origin.current){useSession.getState().clear();qc.clear()}
    committing.current=false;setFinishing(false);setPending(null);completion=undefined;clearOAuthMarkers();setError('Authorization outcome is unknown. Sign in with Google and your owner PIN again. The previous submission will not be retried.');navigate('/?owner_pin_uncertain=1',{replace:true})
  }
  useBackHandler(()=>{cancelOwner();return true},!!pending)
  if(pending)return <main className="page-pad flex flex-col gap-5" style={{maxWidth:'48rem',margin:'auto'}} data-testid="pending-owner-login">
    <h1 className="display">Finish owner sign-in</h1>
    <FocusGroup focusKey="owner-login" className="flex flex-col gap-4">
      {setup?<OwnerPinSetup recovery={!pending.setup_required} key={pending.profile.id} pendingToken={pending.pending_token} onCommitting={critical} onUncertain={uncertain} onApproved={finishOwner}/>:<OwnerPin targetId={pending.profile.id} targetName={pending.profile.name} action="login" pendingToken={pending.pending_token} onCommitting={critical} onUncertain={uncertain} onApproved={finishOwner}/>}
      {!pending.setup_required&&<Button focusKey="owner-login:mode" disabled={finishing} onEnter={()=>setSetup(value=>!value)}>{setup?"Back to PIN entry":"Owner PIN setup or recovery"}</Button>}
      <Button focusKey="owner-login:cancel" disabled={finishing} onEnter={cancelOwner}>Cancel owner sign-in</Button>
    </FocusGroup>
  </main>
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
