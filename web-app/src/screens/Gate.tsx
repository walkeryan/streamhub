import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok, unwrap, ApiError } from '../api/client'
import { useDisclaimer, useProfiles } from '../api/hooks'
import type { AuthTokens, Profile } from '../api/types'
import { Button, ErrorState, Modal } from '../components/ui'
import { Icon } from '../components/Icon'
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
    <div role="dialog" aria-modal="true" aria-label="Legal notice" data-testid="disclaimer-gate"
      style={{ position: overlay ? 'fixed' : 'relative', inset: 0, zIndex: 100, minHeight: '100dvh', background: 'var(--t-bg)', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }}>
      <FocusGroup focusKey="disc" boundary className="flex flex-col gap-6" style={{ width: 'min(46rem, 100%)' }}>
        <div className="flex items-center gap-3"><Icon name="info" size={28} /><h1 className="display" style={{ fontSize: 'var(--text-title)' }}>Before you continue</h1></div>
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : !data ? <p className="muted">Loading notice…</p> : declined ? (
          <>
            <p className="heading">You declined the notice.</p>
            <p className="muted">StreamHub stays locked until you accept. You can close this tab, or read the notice again.</p>
            <div><Button focusKey="disc:accept" primary onEnter={() => { setDeclined(false); setLeft(3) }}>Read the notice again</Button></div>
          </>
        ) : (
          <>
            <div className="panel flex flex-col gap-4" style={{ lineHeight: 1.55 }}>
              {data.text.split(/\n{2,}/).map((para, i) => <p key={i}>{renderInline(para)}</p>)}
              <p className="label">Notice version {data.version}</p>
            </div>
            {err && <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p>}
            <div className="flex gap-3 flex-wrap items-center">
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

/* ------------------------------ profile picker / login ------------------------------ */
export function ProfilePicker() {
  const { data: profiles, error, refetch } = useProfiles()
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const [pending, setPending] = useState<Profile | null>(null)
  const [secret, setSecret] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { if (profiles) { const id = window.setTimeout(() => void setFocus(`profile:${profiles[0]?.id}`), 50); return () => window.clearTimeout(id) } }, [profiles])

  const login = async (p: Profile, s?: string) => {
    setBusy(true); setErr(null)
    try {
      const t = await unwrap<AuthTokens>(api.POST('/auth/login', { body: { profile_id: p.id, ...(p.is_admin ? { password: s ?? null } : { pin: s ?? null }) } }))
      setSession(t.access_token, t.refresh_token, t.profile)
      qc.clear()
      setPending(null); setSecret('')
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 401 ? (p.is_admin ? 'That password is not right.' : 'That PIN is not right.') : e instanceof Error ? e.message : 'Sign-in failed.')
    } finally { setBusy(false) }
  }
  const choose = (p: Profile) => {
    if (p.is_admin || p.has_pin) { setPending(p); setSecret(''); setErr(null) } else void login(p)
  }

  return (
    <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }} data-testid="profile-picker">
      <FocusGroup focusKey="picker" className="flex flex-col items-center gap-8">
        <h1 className="display" style={{ textAlign: 'center' }}>Who is watching?</h1>
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : !profiles ? <p className="muted">Loading profiles…</p> : (
          <div className="flex gap-6 flex-wrap justify-center">
            {profiles.map((p) => (
              <FocusGroup key={p.id} className="">
                <ProfileCard p={p} onEnter={() => choose(p)} />
              </FocusGroup>
            ))}
          </div>
        )}
      </FocusGroup>
      <Modal open={!!pending} onClose={() => setPending(null)} title={pending ? (pending.is_admin ? `Password for ${pending.name}` : `PIN for ${pending.name}`) : ''} firstFocusKey="login:input">
        {pending && (
          <>
            <FocusableInput focusKey="login:input" label={pending.is_admin ? 'Password' : 'PIN'} type="password" value={secret}
              inputMode={pending.is_admin ? 'text' : 'numeric'} onChange={setSecret} onSubmit={() => void login(pending, secret)} />
            {err && <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p>}
            <div className="flex gap-3">
              <Button focusKey="login:go" primary disabled={busy || !secret} onEnter={() => void login(pending, secret)}>Sign in</Button>
              <Button focusKey="login:cancel" onEnter={() => setPending(null)}>Cancel</Button>
            </div>
          </>
        )}
      </Modal>
    </main>
  )
}

function ProfileCard({ p, onEnter }: { p: Profile; onEnter: () => void }) {
  return (
    <Focusable focusKey={`profile:${p.id}`} onEnter={onEnter} label={p.name} data-testid="profile-card" className="flex flex-col items-center gap-3 p-4 rounded-lg">
      <span style={{ width: '6rem', height: '6rem', borderRadius: 999, display: 'grid', placeItems: 'center', fontSize: '2.25rem', fontWeight: 700, background: 'var(--t-accent)', color: 'var(--t-accent-text)' }}>
        {p.name.slice(0, 1).toUpperCase()}
      </span>
      <span className="heading">{p.name}</span>
      <span className="label">{p.kids ? 'Kids' : p.is_admin ? 'Admin' : p.has_pin ? 'PIN protected' : ' '}</span>
    </Focusable>
  )
}
