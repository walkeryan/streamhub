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
import type { components } from '../api/schema'
import { takeInvited, usePending } from '../store/pending'
import { ProfilePicker } from './Gate'
import { WaitingForApproval } from './Waiting'

type PendingApproval = components['schemas']['PendingApproval']
type Completion = AuthTokens | PendingOwnerLogin | PendingApproval

// React StrictMode remounts effects: consume the one-time handoff only once per page load.
let completion: Promise<Completion> | undefined
// Likewise read once: whether this sign-in set out from an invite link.
let invited: boolean | undefined
const wasInvited = () => (invited ??= takeInvited())
function clearOAuthMarkers() {
  const url = new URL(window.location.href)
  url.searchParams.delete('google_complete')
  url.searchParams.delete('google_error')
  window.history.replaceState(null, '', url.pathname + url.search)
}
// Why the server would not finish a sign-in. The callback reports the first two as ?google_error=<code>.
const REFUSED: Record<string, string> = {
  invite_required: 'New people need an invitation here. Ask the person who runs this StreamHub for an invite link.',
  user_suspended: 'This account has been suspended. Ask the person who runs this StreamHub to reinstate it.',
  owner_pin_required: 'Only the owner signs in with Google on this server.',
}
const INVITE_SPENT = 'That invitation can no longer be used: it has expired, been used already or been withdrawn. Ask for a new link.'
function completeLogin() {
  completion ??= fetch('/api/v1/auth/google/complete', { method: 'POST', credentials: 'same-origin' })
    .then(async (r) => {
      if (!r.ok) {
        const code = await r.json().then((b: { code?: string } | null) => b?.code, () => undefined)
        throw new Error(REFUSED[code ?? ''] ?? 'Sign-in expired. Please try again.')
      }
      return await r.json() as Completion
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
  const waiting = usePending((s) => s.pending)
  const wait = usePending((s) => s.wait)
  const stopWaiting = usePending((s) => s.stop)
  const notice = useSession((s) => s.notice)
  const [error, setError] = useState<string | null>(()=>new URLSearchParams(window.location.search).has('owner_pin_uncertain')?'Authorization outcome is unknown. Sign in with Google and your owner PIN again. The previous submission will not be retried.':null)
  const [busy, setBusy] = useState(false)
  const config = useQuery({ queryKey: ['auth-config'], queryFn: async () => {
    const r = await fetch('/api/v1/auth/config')
    if (!r.ok) throw new Error('Cannot load sign-in settings. Please retry.')
    return await r.json() as { google_enabled: boolean; google_required: boolean; signup_mode?: 'approval' | 'invite_only' }
  } })
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.has('google_error')) {
      const reason = params.get('google_error') ?? ''
      setError(reason === 'invite_required' && wasInvited() ? INVITE_SPENT : REFUSED[reason] ?? 'Google sign-in was canceled or expired. Please try again.')
      clearOAuthMarkers()
    }
    if (!params.has('google_complete')) return
    setBusy(true)
    void completeLogin().then((t) => {
      if ('status' in t && t.status === 'pending' && typeof t.pending_token === 'string') {
        // Signed in, not yet let in: wait on the approval screen with the token that only opens /auth/me.
        clearOAuthMarkers()
        wait({ token: t.pending_token, name: t.user?.display_name ?? 'you', email: t.user?.email ?? null, invited: wasInvited() })
      } else if ('owner_pin_required' in t && t.owner_pin_required && typeof t.pending_token === 'string' && t.profile?.id) {
        origin.current=useSession.getState().generation;setSetup(!!t.setup_required);setPending(t)
      } else if (isAuthTokens(t as OwnerPinResult)) {
        const tokens=t as AuthTokens;clearOAuthMarkers();wasInvited();setSession(tokens.access_token,tokens.refresh_token,tokens.profile);qc.clear()
      } else throw new Error('Sign-in did not return a usable session. Please try again.')
    }).catch((e: unknown) => {
      clearOAuthMarkers()
      setError(e instanceof Error ? e.message : 'Sign-in failed.')
    }).finally(() => setBusy(false))
  }, [qc, setSession, wait])
  useEffect(() => {
    if (!config.data?.google_enabled || pending || waiting) return
    const timer = window.setTimeout(() => void setFocus('google:signin'), 50)
    return () => window.clearTimeout(timer)
  }, [config.data,pending,waiting])
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
  if (waiting) return <WaitingForApproval onSignOut={() => { stopWaiting(); completion = undefined; invited = undefined }} />
  if (config.isPending) return <main className="p-8"><p>Loading sign-in…</p></main>
  if (config.error) return <main className="p-8"><ErrorState error={config.error} onRetry={() => void config.refetch()} /></main>
  if (!config.data.google_required && !config.data.google_enabled) return <>{notice && <p role="alert" className="page-pad" data-testid="signed-out-notice">{notice}</p>}<ProfilePicker /></>
  return <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }}>
    <FocusGroup focusKey="google-login" className="flex flex-col items-center gap-6">
      <h1 className="display" style={{ textAlign: 'center' }}>Welcome to StreamHub</h1>
      <p style={{ textAlign: 'center' }}>Sign in with Google to watch or pair your TV.</p>
      {config.data.google_enabled && <p className="muted" style={{ textAlign: 'center', maxWidth: '34rem' }}>{config.data.signup_mode === 'invite_only'
        ? 'New here? You need an invite link from the person who runs this StreamHub.'
        : 'New here? Sign in, and the person who runs this StreamHub is asked to approve you.'}</p>}
      {notice && <p role="alert" data-testid="signed-out-notice">{notice}</p>}
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
