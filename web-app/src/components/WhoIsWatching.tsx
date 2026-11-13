import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, absent } from '../api/client'
import { people, type Switchable } from '../api/people'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useBackHandler, useOverlayFocus } from '../nav/back'
import { useSession } from '../store/session'
import { useWho } from '../store/who'
import { Avatar } from './Avatar'
import { Icon } from './Icon'
import { PinDialog } from './PinPad'
import { Button, ErrorState } from './ui'

/**
 * The picker a shared household device opens on, and shows again from the profile entry. Choosing
 * someone switches this device's session to them: the server asks for their PIN when they have one,
 * and refuses a switch to wider access when they have none. `mandatory` is the picker at the start
 * of the app, which has no way out but choosing.
 */
export function WhoIsWatching({ users, mandatory, error, onRetry }: { users: Switchable[] | undefined; mandatory?: boolean; error?: unknown; onRetry?: () => void }) {
  const pick = useWho((s) => s.pick)
  const hide = useWho((s) => s.hide)
  const setSession = useSession((s) => s.setSession)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [asking, setAsking] = useState<Switchable | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const first = `who:${(users?.find((u) => u.current) ?? users?.[0])?.id ?? 'retry'}`
  // At the start there is nothing behind the picker to go back to; later, Back closes it.
  useBackHandler(() => { if (!mandatory && !asking) hide() }, !asking)
  useOverlayFocus(true, first)
  useEffect(() => {
    document.body.classList.add('no-scroll')
    return () => document.body.classList.remove('no-scroll')
  }, [])

  const switchTo = async (u: Switchable, pin?: string) => {
    const t = await people.switchTo(u.id, pin)
    // Everything cached belongs to the person who was watching.
    await qc.cancelQueries()
    qc.clear()
    pick()
    setSession(t.access_token, t.refresh_token, t.profile)
    navigate('/', { replace: true })
  }
  const refused = (u: Switchable) => (code: string | undefined) =>
    code === 'pin_setup_required' ? `${u.display_name} has no PIN yet, and this device cannot switch to wider access without one. It is set on the Users screen.`
      : code === 'not_shared_device' ? 'This device is no longer set up as a shared household device.'
      : undefined
  const choose = async (u: Switchable) => {
    if (busy) return
    setProblem(null)
    if (u.has_pin) { setAsking(u); return }
    // Staying as the person already watching needs no switch.
    if (u.current) { pick(); return }
    setBusy(true)
    try { await switchTo(u) } catch (e) {
      const code = e instanceof ApiError ? e.code : undefined
      if (code === 'pin_required') setAsking(u)
      else if (absent(e)) { setProblem('This server cannot switch users yet.'); if (!mandatory) hide() }
      else setProblem(refused(u)(code) ?? (e instanceof ApiError && e.status === 429 ? 'Too many attempts. Wait a few minutes and try again.' : e instanceof Error ? e.message : 'Could not switch.'))
    } finally { setBusy(false) }
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Who's watching?" className="who" data-testid="who-is-watching" data-mandatory={!!mandatory}>
      <FocusGroup focusKey="who" boundary className="flex flex-col items-center gap-8" style={{ width: 'min(60rem, 100%)' }}>
        <h1 className="display" style={{ textAlign: 'center' }}>Who's watching?</h1>
        {error ? <ErrorState error={error} onRetry={onRetry} /> : !users ? <p className="muted">Loading…</p> : (
          <div className="who-grid">
            {users.map((u) => (
              <Focusable key={u.id} focusKey={`who:${u.id}`} className="who-card" label={`${u.display_name}${u.has_pin ? ', PIN needed' : ''}${u.current ? ', watching now' : ''}`}
                data-testid="who-card" data={{ current: u.current, locked: u.has_pin }} onEnter={() => void choose(u)}>
                <span className="who-face">
                  <Avatar name={u.display_name} avatar={u.avatar} picture={u.picture} size={6} />
                  {u.has_pin && <span className="who-lock" aria-hidden="true" data-testid="who-lock"><Icon name="lock" size={16} /></span>}
                </span>
                <span className="heading who-name">{u.display_name}</span>
                <span className="label">{u.current ? 'Watching now' : u.role_name ?? ' '}</span>
              </Focusable>
            ))}
          </div>
        )}
        {problem && <p role="alert" data-testid="who-problem" style={{ color: 'var(--t-danger)', textAlign: 'center', maxWidth: '34rem' }}>{problem}</p>}
        {!mandatory && <Button focusKey="who:close" onEnter={hide} testId="who-close">Cancel</Button>}
      </FocusGroup>
      <PinDialog open={!!asking} onClose={() => setAsking(null)} title={`PIN for ${asking?.display_name ?? ''}`} hint="Enter their PIN to switch this device to them."
        submitLabel="Switch" explain={asking ? refused(asking) : undefined} onSubmit={(pin) => switchTo(asking!, pin)} />
    </div>
  )
}
