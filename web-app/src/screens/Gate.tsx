import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok, unwrap, ApiError } from '../api/client'
import { useDisclaimer, useProfiles } from '../api/hooks'
import type { AuthTokens, Profile } from '../api/types'
import { Button, ErrorState, Modal } from '../components/ui'
import { Icon } from '../components/Icon'
import { Avatar } from '../components/Avatar'
import { GlassPanel } from '../ds'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'

/* ------------------------------ disclaimer ------------------------------ */
function renderInline(s: string) {
  return s.split(/(\*\*[^*]+\*\*)/g).map((part, i) => (part.startsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part))
}

export function DisclaimerGate({ onAccepted, onExit, overlay }: { onAccepted: () => void; onExit?: () => void; overlay?: boolean }) {
  const { data, error, refetch } = useDisclaimer()
  const [left, setLeft] = useState(3)
  const [busy, setBusy] = useState(false)
  const [declined, setDeclined] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (left <= 0) return
    const id = window.setTimeout(() => setLeft((n) => n - 1), 1000)
    return () => window.clearTimeout(id)
  }, [left])
  useEffect(() => { const id = window.setTimeout(() => void setFocus('disc:accept'), 60); return () => window.clearTimeout(id) }, [data, declined])

  const accept = async () => {
    if (!data || left > 0) return
    setBusy(true)
    try {
      await ok(api.POST('/disclaimer/accept', { body: { version: data.version } }))
      onAccepted()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save your acceptance.')
    } finally { setBusy(false) }
  }
  const exit = () => {
    if (onExit) return onExit()
    try { window.close() } catch { /* tabs opened by the user cannot be closed by scripts */ }
    setDeclined(true)
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Legal notice" data-testid="disclaimer-gate" className="auth-screen"
      style={overlay ? { position: 'fixed', inset: 0, zIndex: 100, overflow: 'auto' } : undefined}>
      <FocusGroup focusKey="disc" boundary className="auth-col auth-col-wide">
        <span className="auth-mark" aria-hidden="true">Stream<b>Hub</b></span>
        <h1 className="auth-title">Before you continue</h1>
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : !data ? <p className="acct-note">Loading notice…</p> : declined ? (
          <GlassPanel className="acct-group">
            <h2 className="ds-section-title">You declined the notice.</h2>
            <p className="acct-note">StreamHub stays locked until you accept. You can close this tab, or read the notice again.</p>
            <div><Button focusKey="disc:accept" primary onEnter={() => { setDeclined(false); setLeft(3) }}>Read the notice again</Button></div>
          </GlassPanel>
        ) : (
          <>
            <div className="auth-panel legal-text">
              {data.text.split(/\n{2,}/).map((para, i) => <p key={i}>{renderInline(para)}</p>)}
              <p className="acct-kicker">Notice version {data.version}</p>
            </div>
            {err && <p role="alert" className="acct-error">{err}</p>}
            <div className="acct-actions">
              <Button focusKey="disc:accept" primary disabled={left > 0 || busy} onEnter={accept} testId="disclaimer-accept">
                {left > 0 ? `I accept (${left})` : 'I accept'}
              </Button>
              <Button focusKey="disc:exit" onEnter={exit} testId="disclaimer-exit">Exit</Button>
            </div>
          </>
        )}
      </FocusGroup>
    </div>
  )
}

/* ------------------------------ away from home ------------------------------ */
/**
 * A user without remote.access, away from the home network: the server answers nothing but /auth/me,
 * so this stands in for the whole app. Try again asks once more (the device may be home by now).
 */
export function AwayFromHome({ name, onSignOut }: { name: string; onSignOut: () => void }) {
  const setAway = useSession((s) => s.setAway)
  const qc = useQueryClient()
  useEffect(() => { const id = window.setTimeout(() => void setFocus('away:retry'), 50); return () => window.clearTimeout(id) }, [])
  return (
    <main className="auth-screen" data-testid="away-from-home">
      <FocusGroup focusKey="away" boundary className="auth-col">
        <span className="auth-mark" aria-hidden="true">Stream<b>Hub</b></span>
        <h1 className="auth-title">Only on the home network</h1>
        <div className="auth-panel">
          <p role="alert">This account can only be used on the home network, and this device is somewhere else.</p>
          <p className="acct-note">Signed in as {name}. Connect to the home network and try again, or ask the person who runs this StreamHub to let you watch away from home.</p>
          <div className="acct-actions">
            <Button focusKey="away:retry" primary icon="refresh" onEnter={() => { setAway(false); void qc.invalidateQueries() }} testId="away-retry">Try again</Button>
            <Button focusKey="away:out" onEnter={() => { onSignOut(); qc.clear() }}>Sign out</Button>
          </div>
        </div>
      </FocusGroup>
    </main>
  )
}

/* ------------------------------ profile picker / login ------------------------------ */
/** `embedded`: under the ways to sign in on the sign-in screen, where it is one section and does not take the cursor. */
export function ProfilePicker({ embedded }: { embedded?: boolean } = {}) {
  const { data: profiles, error, refetch } = useProfiles()
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const [pending, setPending] = useState<Profile | null>(null)
  const [secret, setSecret] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { if (profiles && !embedded) { const id = window.setTimeout(() => void setFocus(`profile:${profiles[0]?.id}`), 50); return () => window.clearTimeout(id) } }, [profiles, embedded])

  const login = async (p: Profile, s?: string) => {
    setBusy(true); setErr(null)
    try {
      const t = await unwrap<AuthTokens>(api.POST('/auth/login', { body: { profile_id: p.id, ...(p.has_password ? { password: s ?? null } : { pin: s ?? null }) } }))
      setSession(t.access_token, t.refresh_token, t.profile)
      qc.clear()
      setPending(null); setSecret('')
    } catch (e) {
      // A profile with no PIN that the server will not open on a tap: its person signs in for themselves, or manages the server.
      const tapRefused = !p.has_password && !p.has_pin ? `${p.name} cannot be opened by choosing the profile. Sign in another way, or have a PIN set for it.` : null
      setErr(e instanceof ApiError && e.status === 401 ? tapRefused ?? (p.has_password ? 'That password is not right.' : 'That PIN is not right.') : e instanceof Error ? e.message : 'Sign-in failed.')
    } finally { setBusy(false) }
  }
  const choose = (p: Profile) => {
    // Which secret a profile signs in with is what the server says it has: a password, a PIN, or neither.
    if (p.has_password || p.has_pin) { setPending(p); setSecret(''); setErr(null) } else void login(p)
  }

  return (
    <main className={embedded ? 'picker-embedded' : 'picker-screen'} data-testid="profile-picker">
      <FocusGroup focusKey="picker" className="who-col">
        {embedded ? <h2 className="who-title">At home? Pick a profile</h2> : <h1 className="who-title">Who is watching?</h1>}
        {err && !pending && <p role="alert" data-testid="profile-refused" className="acct-error" style={{ textAlign: 'center' }}>{err}</p>}
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : !profiles ? <p className="acct-note">Loading profiles…</p> : (
          <div className="who-grid">
            {profiles.map((p) => (
              <FocusGroup key={p.id} className="">
                <ProfileCard p={p} size={embedded ? 4.5 : 7} onEnter={() => choose(p)} />
              </FocusGroup>
            ))}
          </div>
        )}
      </FocusGroup>
      {/* A profile whose PIN was chosen as a password is typed as text; an arrow pattern is typed as its digits. */}
      <Modal open={!!pending} onClose={() => setPending(null)} title={pending ? (pending.has_password || pending.pin_kind === 'password' ? `Password for ${pending.name}` : `PIN for ${pending.name}`) : ''} firstFocusKey="login:input">
        {pending && (
          <div className="secret-form">
            <div className="acct-field">
              <span className="acct-kicker" aria-hidden="true">{pending.has_password || pending.pin_kind === 'password' ? 'Password' : 'PIN'}</span>
              <FocusableInput focusKey="login:input" label={pending.has_password || pending.pin_kind === 'password' ? 'Password' : 'PIN'} type="password" value={secret}
                inputMode={pending.has_password || pending.pin_kind === 'password' ? 'text' : 'numeric'} onChange={setSecret} onSubmit={() => void login(pending, secret)} />
            </div>
            {err && <p role="alert" className="acct-error">{err}</p>}
            <div className="acct-actions">
              <Button focusKey="login:go" primary disabled={busy || !secret} onEnter={() => void login(pending, secret)}>Sign in</Button>
              <Button focusKey="login:cancel" onEnter={() => setPending(null)}>Cancel</Button>
            </div>
          </div>
        )}
      </Modal>
    </main>
  )
}

/** A profile as on "Who's watching?": the large face (ringed when focused), the name, then what it is. */
function ProfileCard({ p, size, onEnter }: { p: Profile; size: number; onEnter: () => void }) {
  return (
    <Focusable focusKey={`profile:${p.id}`} onEnter={onEnter} label={p.name} data-testid="profile-card" className="who-card">
      <span className="who-face">
        <Avatar name={p.name} size={size} />
        {(p.has_pin || p.has_password) && <span className="who-lock" aria-hidden="true"><Icon name="lock" size={16} /></span>}
      </span>
      <span className="who-name">{p.name}</span>
      <span className="who-role">{p.kids ? 'Kids' : p.is_admin ? 'Admin' : p.has_pin ? 'PIN protected' : ' '}</span>
    </Focusable>
  )
}
