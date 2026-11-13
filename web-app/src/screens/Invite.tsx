import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { API_BASE } from '../api/client'
import { useAuthCapabilities } from '../api/contentPolicy'
import { goToPlex, passwordProblem, signin, signInError, usernameProblem } from '../api/signin'
import { Button, ErrorState } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { markInvited } from '../store/pending'
import { useSession } from '../store/session'

// The server's invite tokens: 43 URL-safe characters. Anything else is a damaged link, not worth a sign-in.
const TOKEN = /^[A-Za-z0-9_-]{43}$/

/**
 * /invite/<token>: says what the link is, then offers the ways to accept it that this server has
 * switched on: Google, Plex, or choosing a username and a password. A setup link (the owner's
 * password reset) is the same page with one way only. A link that can no longer be used is said so
 * here when the server can tell; an older server can only tell at sign-in (see LoginGate).
 */
export function InviteLanding({ token }: { token: string }) {
  const profile = useSession((s) => s.profile)
  const signOut = useSession((s) => s.clear)
  const setSession = useSession((s) => s.setSession)
  const config = useAuthCapabilities()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const valid = TOKEN.test(token)
  const look = useQuery({ queryKey: ['invite-lookup', token], enabled: valid && !profile, retry: false, staleTime: Infinity, queryFn: ({ signal }) => signin.lookupInvite(token, signal) })
  const [choosing, setChoosing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const c = config.data
  const setup = !!look.data?.setup
  const dead = look.data?.valid === false
  const google = !!c?.google_enabled && !setup, plex = !!c?.plex_enabled && !setup, password = !!c?.password_enabled
  const form = choosing || (setup && password)
  const first = profile ? 'invite:home' : !valid || dead ? 'invite:signin' : form ? (look.data?.username ? 'invite:password' : 'invite:username')
    : google ? 'invite:accept' : plex ? 'invite:plex' : password ? 'invite:choose' : 'invite:signin'
  const ready = !config.isPending && !look.isPending
  useEffect(() => { if (!ready && !profile && valid) return; const id = window.setTimeout(() => void setFocus(first), 50); return () => window.clearTimeout(id) }, [first, ready, profile, valid])
  const accept = () => {
    markInvited()
    window.location.assign(`${API_BASE}/auth/google/start?return_to=${encodeURIComponent('/')}&invite=${encodeURIComponent(token)}`)
  }
  const acceptWithPlex = () => {
    markInvited(); setError(null); setBusy(true)
    goToPlex('/', { invite: token }).catch((e: unknown) => { setBusy(false); setError(signInError(e, 'Plex sign-in could not be started.')) })
  }
  const signedIn = (t: Awaited<ReturnType<typeof signin.acceptInvite>>) => { qc.clear(); setSession(t.access_token, t.refresh_token, t.profile); navigate('/', { replace: true }) }
  const who = look.data?.display_name
  const goHome = <div><Button focusKey="invite:signin" onEnter={() => navigate('/', { replace: true })}>Go to sign-in</Button></div>
  let body
  if (profile) body = <>
    <h1 className="display">You already have an account</h1>
    <p>You are signed in as <strong>{profile.name}</strong>. An invitation is for someone who is not a user here yet.</p>
    <p className="muted">To accept it as someone else, sign out first and open the link again.</p>
    <div className="flex gap-3 flex-wrap">
      <Button focusKey="invite:home" primary onEnter={() => navigate('/', { replace: true })}>Continue to StreamHub</Button>
      <Button focusKey="invite:out" onEnter={() => { signOut(); qc.clear() }}>Sign out</Button>
    </div>
  </>
  else if (!valid) body = <>
    <h1 className="display">This invitation link is not complete</h1>
    <p role="alert">Part of the link is missing or changed. Open the whole link again, or ask the person who sent it for a new one.</p>
    {goHome}
  </>
  else if (config.error) body = <ErrorState error={config.error} onRetry={() => void config.refetch()} />
  else if (look.error) body = <ErrorState error={look.error} onRetry={() => void look.refetch()} />
  else if (!ready) body = <p className="muted">Loading sign-in…</p>
  else if (dead) body = <>
    <h1 className="display">This link can no longer be used</h1>
    <p role="alert" data-testid="invite-dead">It has expired, been used already or been withdrawn. Ask the person who sent it for a new one.</p>
    {goHome}
  </>
  else if (form) body = <>
    <h1 className="display">{setup ? 'Choose your password' : 'Choose a username and password'}</h1>
    <p>{setup
      ? <>The person who runs this StreamHub sent you this link{who ? <>, <strong>{who}</strong></> : null}. Choose a password and you are signed in.</>
      : <>You sign in to this StreamHub with them from now on. There is no e-mail recovery: if you forget the password, the person who invited you can send a new link.</>}</p>
    <ChoosePassword token={token} fixedUsername={look.data?.username ?? null} onDone={signedIn} onBack={setup ? undefined : () => { setChoosing(false); setError(null) }} />
  </>
  else body = <>
    <h1 className="display">{who ? `${who}, you are invited to StreamHub` : 'You are invited to StreamHub'}</h1>
    <p>The person who runs this StreamHub sent you this link. Accept it and you can start watching straight away, with the access they chose for you.</p>
    <p className="muted">An invitation works a set number of times and then expires.{look.data === null ? ' If this one has run out, you are told after signing in.' : ''}</p>
    {google || plex || password
      ? <FocusGroup focusKey="invite:ways" className="flex flex-col gap-3" style={{ width: 'min(22rem, 100%)' }} role="group" label="Ways to accept">
        {google && <Button focusKey="invite:accept" primary disabled={busy} onEnter={accept} testId="invite-accept">Accept with Google</Button>}
        {plex && <Button focusKey="invite:plex" primary={!google} disabled={busy} onEnter={acceptWithPlex} testId="invite-plex">Accept with Plex</Button>}
        {password && <Button focusKey="invite:choose" primary={!google && !plex} disabled={busy} onEnter={() => { setError(null); setChoosing(true) }} testId="invite-password">Choose a username and password</Button>}
      </FocusGroup>
      : <><p role="alert">{c?.google_enabled === false && c.plex_enabled === undefined
        ? 'Google sign-in is not set up on this server yet, so the invitation cannot be accepted. Tell the person who sent it.'
        : 'No way of signing in is switched on for this server yet, so the invitation cannot be accepted. Tell the person who sent it.'}</p>{goHome}</>}
    {error && <p role="alert">{error}</p>}
  </>
  return (
    <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }} data-testid="invite">
      <FocusGroup focusKey="invite" boundary className="flex flex-col gap-5" style={{ width: 'min(38rem, 100%)' }}>{body}</FocusGroup>
    </main>
  )
}

/** The username and password behind an invitation or a setup link. What the server would refuse is said before it is asked. */
function ChoosePassword({ token, fixedUsername, onDone, onBack }: { token: string; fixedUsername: string | null; onDone: (tokens: Awaited<ReturnType<typeof signin.acceptInvite>>) => void; onBack?: () => void }) {
  const [username, setUsername] = useState(fixedUsername ?? '')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const name = (fixedUsername ?? username).trim().toLowerCase()
  const submit = async () => {
    if (busy) return
    const problem = (fixedUsername ? null : usernameProblem(name)) ?? passwordProblem(name, password, again)
    if (problem) { setError(problem); return }
    setBusy(true); setError(null)
    try { onDone(await signin.acceptInvite(token, name, password)) }
    catch (e) { setError(signInError(e, 'That did not work. Please try again.')) }
    finally { setBusy(false) }
  }
  return <FocusGroup focusKey="invite:form" className="flex flex-col gap-3" style={{ width: 'min(24rem, 100%)' }} role="group" label="Username and password" data-testid="invite-form">
    {fixedUsername
      ? <p data-testid="invite-username">Your username is <strong>{fixedUsername}</strong>.</p>
      : <FocusableInput focusKey="invite:username" label="Username" placeholder="Username" maxLength={32} autoCapitalize="none" autoComplete="username" value={username} onChange={setUsername} onSubmit={() => void setFocus('invite:password')} />}
    <FocusableInput focusKey="invite:password" label="Password" placeholder="At least 10 characters" type="password" maxLength={256} autoComplete="new-password" value={password} onChange={setPassword} onSubmit={() => void setFocus('invite:again')} />
    <FocusableInput focusKey="invite:again" label="Password again" placeholder="The same password again" type="password" maxLength={256} autoComplete="new-password" value={again} onChange={setAgain} onSubmit={() => void submit()} />
    <p className="label">3 to 32 letters, digits, dots, dashes or underscores for the username. At least 10 characters for the password, not containing the username.</p>
    {error && <p role="alert" data-testid="invite-error">{error}</p>}
    <div className="flex gap-3 flex-wrap">
      <Button focusKey="invite:save" primary disabled={busy} onEnter={() => void submit()} testId="invite-save">{busy ? 'Saving…' : 'Save and sign in'}</Button>
      {onBack && <Button focusKey="invite:back" onEnter={onBack}>Other ways to accept</Button>}
    </div>
  </FocusGroup>
}
