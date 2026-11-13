import { useEffect, useRef, useState } from 'react'
import { useBackHandler } from '../nav/back'
import { useNavigate } from 'react-router'
import { OwnerPin } from '../components/OwnerPin'
import { OwnerPinSetup } from '../components/OwnerPinSetup'
import { isAuthTokens, type PendingOwnerLogin, type OwnerPinResult } from '../api/ownerPin'
import { useQueryClient } from '@tanstack/react-query'
import { useAuthCapabilities } from '../api/contentPolicy'
import { Button, ErrorState } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'
import type { AuthTokens } from '../api/types'
import type { components } from '../api/schema'
import { takeInvited, usePending } from '../store/pending'
import { finishPlex, goToPlex, signin, signInError, takePlexState } from '../api/signin'
import { FocusableInput } from '../nav/Focusable'
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
// Plex hands the browser back with ?plex=<state>, once per page load; it is read once however often React mounts this.
let plexReturn: string | null | undefined
const plexReturned = () => (plexReturn === undefined ? (plexReturn = takePlexState()) : plexReturn)
/** Where a sign-in that leaves the page should come back to: the pairing screen keeps its code, anything else is the start page. */
function returnPath(): string {
  const code = new URLSearchParams(window.location.search).get('code')?.toUpperCase() ?? ''
  return window.location.pathname === '/pair' ? '/pair' + (/^[A-Z0-9]{6}$/.test(code) ? `?code=${code}` : '') : '/'
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
  const config = useAuthCapabilities()
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
  // Back from Plex: the same three endings as Google's, without the owner-PIN regime (which is Google's alone).
  useEffect(() => {
    const state = plexReturned()
    if (!state) return
    plexReturn = null
    setBusy(true)
    void finishPlex(state).then((t) => {
      if (t.status === 'pending') wait({ token: t.pending_token, name: t.user?.display_name ?? 'you', email: t.user?.email ?? null, invited: wasInvited() })
      else if (t.status === 'active') { wasInvited(); setSession(t.access_token, t.refresh_token, t.profile); qc.clear() }
      else throw new Error('That Plex sign-in was started to link an account. Sign in, then link Plex from Settings.')
    }).catch((e: unknown) => {
      const refused = (e as { code?: string }).code === 'invite_required' && wasInvited()
      setError(refused ? INVITE_SPENT : signInError(e, 'Plex sign-in failed. Please try again.'))
    }).finally(() => setBusy(false))
  }, [qc, setSession, wait])
  const [typing, setTyping] = useState(false)
  const first = config.data?.google_enabled ? 'google:signin' : config.data?.plex_enabled ? 'plex:signin' : config.data?.password_enabled ? 'password:open' : null
  useEffect(() => {
    if (!first || pending || waiting) return
    const timer = window.setTimeout(() => void setFocus(typing ? 'password:username' : first), 50)
    return () => window.clearTimeout(timer)
  }, [first,pending,waiting,typing])
  const plex = () => { setError(null); setBusy(true); goToPlex(returnPath()).catch((e: unknown) => { setBusy(false); setError(signInError(e, 'Plex sign-in could not be started.')) }) }
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
  const c = config.data
  // An older server always offered the profile picker unless Google was required; a current one says
  // whether this browser may use it (it is for the home network).
  const profiles = c.profiles_enabled ?? !c.google_required
  const ways = [c.google_enabled, !!c.plex_enabled, !!c.password_enabled].filter(Boolean).length
  if (!ways && profiles && !error && !busy) return <>{notice && <p role="alert" className="page-pad" data-testid="signed-out-notice">{notice}</p>}<ProfilePicker /></>
  return <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }} data-testid="sign-in">
    <FocusGroup focusKey="google-login" className="flex flex-col items-center gap-6" style={{ width: 'min(34rem, 100%)' }}>
      <h1 className="display" style={{ textAlign: 'center' }}>Welcome to StreamHub</h1>
      <p style={{ textAlign: 'center' }}>{ways === 1 && c.google_enabled ? 'Sign in with Google to watch or pair your TV.' : 'Sign in to watch or pair your TV.'}</p>
      {(c.google_enabled || c.plex_enabled) && <p className="muted" style={{ textAlign: 'center' }}>{c.signup_mode === 'invite_only'
        ? 'New here? You need an invite link from the person who runs this StreamHub.'
        : 'New here? Sign in, and the person who runs this StreamHub is asked to approve you.'}</p>}
      {notice && <p role="alert" data-testid="signed-out-notice">{notice}</p>}
      {typing ? <PasswordSignIn onBack={() => { setTyping(false); setError(null) }} onSignedIn={(t) => { setSession(t.access_token, t.refresh_token, t.profile); qc.clear() }} />
        : <FocusGroup focusKey="signin-ways" className="flex flex-col gap-3" style={{ width: 'min(22rem, 100%)' }} role="group" label="Ways to sign in">
          {c.google_enabled && <Button primary focusKey="google:signin" disabled={busy} testId="signin-google" onEnter={() => {
            window.location.assign(`/api/v1/auth/google/start?return_to=${encodeURIComponent(returnPath())}`)
          }}>{busy ? 'Completing sign-in…' : 'Continue with Google'}</Button>}
          {c.plex_enabled && <Button primary={!c.google_enabled} focusKey="plex:signin" disabled={busy} onEnter={plex} testId="signin-plex">{busy && !c.google_enabled ? 'Completing sign-in…' : 'Continue with Plex'}</Button>}
          {c.password_enabled && <Button primary={!c.google_enabled && !c.plex_enabled} focusKey="password:open" disabled={busy} onEnter={() => { setError(null); setTyping(true) }} testId="signin-password">Username and password</Button>}
        </FocusGroup>}
      {!ways && <p role="alert">{c.google_required && c.profiles_enabled === undefined
        ? 'Google sign-in is not configured yet. Contact the server owner.'
        : 'No way of signing in from here is switched on. Ask the person who runs this StreamHub.'}</p>}
      {error && <p role="alert" data-testid="signin-error">{error}</p>}
      {profiles && <ProfilePicker embedded />}
    </FocusGroup>
  </main>
}

/** Username and password, in place of the list of ways. A wrong name and a wrong password read the same, as the server answers them. */
function PasswordSignIn({ onBack, onSignedIn }: { onBack: () => void; onSignedIn: (tokens: AuthTokens) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    if (busy || !username.trim() || !password) return
    setBusy(true); setError(null)
    try { onSignedIn(await signin.password(username.trim(), password)) }
    catch (e) { setPassword(''); setError(signInError(e, 'Sign-in failed. Please try again.')); void setFocus('password:password') }
    finally { setBusy(false) }
  }
  return <FocusGroup focusKey="password-form" className="flex flex-col gap-3" style={{ width: 'min(22rem, 100%)' }} role="group" label="Username and password">
    <FocusableInput focusKey="password:username" label="Username" placeholder="Username" maxLength={64} autoCapitalize="none" autoComplete="username" value={username} onChange={setUsername} onSubmit={() => void setFocus('password:password')} />
    <FocusableInput focusKey="password:password" label="Password" placeholder="Password" type="password" maxLength={256} autoComplete="current-password" value={password} onChange={setPassword} onSubmit={() => void submit()} />
    {error && <p role="alert" data-testid="password-error">{error}</p>}
    <div className="flex gap-3 flex-wrap">
      <Button focusKey="password:go" primary disabled={busy || !username.trim() || !password} onEnter={() => void submit()} testId="password-submit">{busy ? 'Signing in…' : 'Sign in'}</Button>
      <Button focusKey="password:back" onEnter={onBack}>Other ways to sign in</Button>
    </div>
    <p className="label">Forgot it? The person who runs this StreamHub can send you a link to choose a new one.</p>
  </FocusGroup>
}
