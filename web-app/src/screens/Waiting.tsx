import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { API_BASE } from '../api/client'
import type { Me } from '../api/me'
import { Button } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { fmtTime } from '../lib/util'
import { usePending } from '../store/pending'
import { useSession } from '../store/session'

const EVERY_MS = 5000
type Outcome = 'waiting' | 'closed' | 'collected'

/**
 * Someone who signed in with Google and is not approved yet. Their token opens GET /auth/me and
 * nothing else; this asks it every five seconds. The answer that follows the approval carries the
 * real tokens, once, and the page becomes a normal session without a second sign-in.
 */
export function WaitingForApproval({ onSignOut }: { onSignOut: () => void }) {
  const pending = usePending((s) => s.pending)!
  const stop = usePending((s) => s.stop)
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState<Outcome>('waiting')
  const [checked, setChecked] = useState<number | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const busy = useRef(false)

  const check = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      const response = await fetch(`${API_BASE}/auth/me`, { headers: { authorization: `Bearer ${pending.token}` }, cache: 'no-store' })
      if (usePending.getState().pending?.token !== pending.token) return
      setUnreachable(false)
      // Refused: the request was declined (the user is gone), or the token ran out.
      if (response.status === 401 || response.status === 403 || response.status === 404) { setOutcome('closed'); return }
      if (!response.ok) { setUnreachable(true); return }
      const me = (await response.json()) as Me
      setChecked(Date.now())
      if (me.tokens) {
        qc.clear()
        stop()
        setSession(me.tokens.access_token, me.tokens.refresh_token, me.tokens.profile)
      } else if (me.status === 'active') setOutcome('collected')
      else if (me.status === 'suspended') setOutcome('closed')
    } catch { setUnreachable(true) } finally { busy.current = false }
  }, [pending.token, qc, setSession, stop])

  useEffect(() => {
    if (outcome !== 'waiting') return
    void check()
    const id = window.setInterval(() => void check(), EVERY_MS)
    return () => window.clearInterval(id)
  }, [check, outcome])
  useEffect(() => { const id = window.setTimeout(() => void setFocus(outcome === 'waiting' ? 'wait:check' : 'wait:out'), 50); return () => window.clearTimeout(id) }, [outcome])

  const who = <p data-testid="waiting-who">Signed in as <strong>{pending.name}</strong>{pending.email ? <span className="muted"> · {pending.email}</span> : null}</p>
  return (
    <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 'var(--safe-y) var(--safe-x)' }} data-testid="waiting" data-outcome={outcome}>
      <FocusGroup focusKey="wait" boundary className="flex flex-col gap-5" style={{ width: 'min(38rem, 100%)' }}>
        {outcome === 'waiting' ? <>
          <h1 className="display">Waiting for approval</h1>
          {who}
          {pending.invited && <p className="panel" role="status">That invitation link could not be used: it has expired, been used already or been withdrawn. Your request went to the owner for approval instead.</p>}
          <section className="panel flex flex-col gap-3">
            <h2 className="heading">What happens next</h2>
            <ol className="wait-steps">
              <li>The person who runs this StreamHub has been told you would like to join.</li>
              <li>They choose what you can watch and approve you.</li>
              <li>This page then opens StreamHub by itself. Keep it open, or come back to it later on this device.</li>
            </ol>
          </section>
          <p className="label" role="status" aria-live="polite">
            {unreachable ? 'The server cannot be reached right now. Still trying.' : checked ? `Not approved yet. Last checked at ${fmtTime(checked)}; checking again every few seconds.` : 'Checking…'}
          </p>
        </> : <>
          <h1 className="display">{outcome === 'collected' ? 'You have been approved' : 'This request is closed'}</h1>
          {who}
          <p role="alert">{outcome === 'collected'
            ? 'Your approval was already picked up, perhaps in another tab. Sign in again to start watching.'
            : 'It was declined, or it waited too long. You can sign in again to ask once more.'}</p>
        </>}
        <div className="flex gap-3 flex-wrap">
          {outcome === 'waiting' && <Button focusKey="wait:check" icon="refresh" onEnter={() => void check()} testId="waiting-check">Check now</Button>}
          <Button focusKey="wait:out" primary={outcome !== 'waiting'} onEnter={onSignOut} testId="waiting-signout">{outcome === 'waiting' ? 'Sign out' : 'Back to sign-in'}</Button>
        </div>
      </FocusGroup>
    </main>
  )
}
