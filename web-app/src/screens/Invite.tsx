import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { API_BASE } from '../api/client'
import { useAuthCapabilities } from '../api/contentPolicy'
import { Button, ErrorState } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { markInvited } from '../store/pending'
import { useSession } from '../store/session'

// The server's invite tokens: 43 URL-safe characters. Anything else is a damaged link, not worth a sign-in.
const TOKEN = /^[A-Za-z0-9_-]{43}$/

/**
 * /invite/<token>: says what the link is, then starts Google sign-in with the invitation attached.
 * Only the sign-in itself can tell whether the invitation is still good, so one that has expired
 * or run out is reported when Google hands the browser back (see LoginGate).
 */
export function InviteLanding({ token }: { token: string }) {
  const profile = useSession((s) => s.profile)
  const signOut = useSession((s) => s.clear)
  const config = useAuthCapabilities()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const valid = TOKEN.test(token)
  const first = profile ? 'invite:home' : valid && config.data?.google_enabled ? 'invite:accept' : 'invite:signin'
  useEffect(() => { const id = window.setTimeout(() => void setFocus(first), 50); return () => window.clearTimeout(id) }, [first])
  const accept = () => {
    markInvited()
    window.location.assign(`${API_BASE}/auth/google/start?return_to=${encodeURIComponent('/')}&invite=${encodeURIComponent(token)}`)
  }
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
    <div><Button focusKey="invite:signin" onEnter={() => navigate('/', { replace: true })}>Go to sign-in</Button></div>
  </>
  else if (config.error) body = <ErrorState error={config.error} onRetry={() => void config.refetch()} />
  else if (config.isPending) body = <p className="muted">Loading sign-in…</p>
  else body = <>
    <h1 className="display">You are invited to StreamHub</h1>
    <p>The person who runs this StreamHub sent you this link. Accept it with your Google account and you can start watching straight away, with the access they chose for you.</p>
    <p className="muted">An invitation works a set number of times and then expires. If this one has run out, you are told after signing in.</p>
    {config.data.google_enabled
      ? <div><Button focusKey="invite:accept" primary onEnter={accept} testId="invite-accept">Accept with Google</Button></div>
      : <><p role="alert">Google sign-in is not set up on this server yet, so the invitation cannot be accepted. Tell the person who sent it.</p>
        <div><Button focusKey="invite:signin" onEnter={() => navigate('/', { replace: true })}>Go to sign-in</Button></div></>}
  </>
  return (
    <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }} data-testid="invite">
      <FocusGroup focusKey="invite" boundary className="flex flex-col gap-5" style={{ width: 'min(38rem, 100%)' }}>{body}</FocusGroup>
    </main>
  )
}
