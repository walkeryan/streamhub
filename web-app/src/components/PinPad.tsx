import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { cn } from '../lib/util'
import { Icon } from './Icon'
import { Button, Modal } from './ui'

const MIN = 4, MAX = 8
/** The keypad, laid out like a phone. Arrows move within this grid only (see `move`). */
const GRID = [['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9'], ['erase', '0', 'ok']] as const
/** Where the remote's focus rests: on open and after every key press, error and confirm step. */
const HOME = 'pin:5'
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

/** What a refused PIN means, for the codes every PIN call shares. A caller can say more for its own. */
function explain(e: unknown, extra?: (code: string | undefined) => string | undefined): string {
  const code = e instanceof ApiError ? e.code : undefined
  return extra?.(code)
    ?? (code === 'pin_invalid' ? 'That PIN is not right. Try again.'
      : code === 'pin_required' ? 'A PIN is needed.'
      : e instanceof ApiError && e.status === 429 ? 'Too many wrong PINs.'
      : e instanceof Error ? e.message : 'That did not work. Try again.')
}

/**
 * The next key for an arrow, clamped to the grid: no wrapping, and never out of the keypad, so a
 * parent entering a PIN blind can count presses from 5 and extra presses at an edge do nothing.
 */
function move(key: string, direction: string): string {
  const r = GRID.findIndex((row) => (row as readonly string[]).includes(key))
  const c = (GRID[r] as readonly string[]).indexOf(key)
  const clamp = (n: number, max: number) => Math.max(0, Math.min(max, n))
  const nr = clamp(r + (direction === 'down' ? 1 : direction === 'up' ? -1 : 0), GRID.length - 1)
  const nc = clamp(c + (direction === 'right' ? 1 : direction === 'left' ? -1 : 0), 2)
  return GRID[nr]![nc]!
}

/**
 * One keypad key. Unlike a Button, erase and OK stay focusable while they have nothing to do (their
 * handlers ignore the press): if they dropped out of the grid, Down, Down, Right from 5 would stop on
 * 0 and the OK press meant for submitting would enter a 0 instead.
 */
function PinKey(p: { k: string; label: string; locked: boolean; inactive?: boolean; primary?: boolean; icon?: string; testId?: string; onEnter: () => void; children?: ReactNode }) {
  return (
    <Focusable
      focusKey={`pin:${p.k}`}
      className={cn('btn pin-key', p.primary && 'btn-primary')}
      label={p.label}
      disabled={p.locked}
      aria={{ disabled: p.locked || p.inactive || undefined }}
      data-testid={p.testId}
      // Scrolling the dialog to follow focus would show where focus is.
      scroll={false}
      onArrow={(direction) => { void setFocus(`pin:${move(p.k, direction)}`); return false }}
      onEnter={p.onEnter}
    >
      {p.icon && <Icon name={p.icon} size={20} />}
      {p.children}
    </Focusable>
  )
}

/**
 * A PIN of four to eight digits, entered on a keypad or a keyboard and never shown. `onSubmit`
 * resolves when the PIN was accepted; when it throws, the dialog stays open and says why. A 429
 * locks the keypad for the server's Retry-After and counts it down. With `confirm`, a new PIN is
 * entered twice before it is submitted.
 *
 * Private entry: people watching the screen while a PIN is entered with the remote must not learn
 * it, so the keypad never shows which key has focus (no ring, lift or colour; see `.pin-pad` in
 * base.css) and focus goes back to 5 after every key press. Each digit is then a short, predictable
 * walk from 5 that the person entering it can do without looking.
 */
export function PinDialog(p: {
  open: boolean; title: string; hint?: string; confirm?: boolean; submitLabel?: string
  onSubmit: (pin: string) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const [pin, setPin] = useState('')
  const [first, setFirst] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const left = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const locked = left > 0
  // Whether the latest input was a typed number key rather than the remote's arrows or OK (see onKey).
  const typed = useRef(false)
  // A fresh dialog each time it opens.
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) { setPin(''); setFirst(null); setError(null); setBusy(false); typed.current = false } }

  useEffect(() => {
    if (!locked) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [locked])
  // The keypad comes back under the remote once the lock ends.
  useEffect(() => { if (p.open && !locked && lockedUntil) void setFocus(HOME) }, [locked, lockedUntil, p.open])

  const ready = pin.length >= MIN && !busy && !locked
  const submit = async () => {
    if (!ready) return
    if (p.confirm && first === null) { setFirst(pin); setPin(''); setError(null); void setFocus(HOME); return }
    if (p.confirm && first !== pin) { setFirst(null); setPin(''); setError('Those two PINs are not the same. Start again.'); void setFocus(HOME); return }
    setBusy(true); setError(null)
    try { await p.onSubmit(pin) } catch (e) {
      setPin(''); setFirst(null)
      setError(explain(e, p.explain))
      // Locked keys cannot hold focus, so the remote waits on Cancel until the lock ends.
      if (e instanceof ApiError && e.status === 429) { setNow(Date.now()); setLockedUntil(Date.now() + (e.retryAfter ?? 60) * 1000); void setFocus('pin:cancel') } else void setFocus(HOME)
    } finally { setBusy(false) }
  }
  const add = (digit: string) => {
    if (busy || locked) return
    setError(null); setPin((v) => (v.length < MAX ? v + digit : v)); void setFocus(HOME)
  }
  const erase = () => { setPin((v) => v.slice(0, -1)); void setFocus(HOME) }

  // A keyboard types the PIN wherever the remote's focus happens to be. Captured ahead of the app's own
  // key handling so Backspace erases rather than going Back while there is something to erase.
  //
  // Enter is shared by a keyboard and the remote's OK, which must press the focused key or a PIN longer
  // than four digits could never be entered by remote. So Enter follows the latest input: after number
  // keys were typed it submits (or does nothing while the PIN is too short); otherwise it is left to the
  // spatial navigation and presses the focused key, entering a digit on a digit key and submitting on the
  // keypad's OK key. An arrow or Tab press hands Enter back to the focused key.
  const live = useRef({ submit, add, erase, pin })
  live.current = { submit, add, erase, pin }
  useEffect(() => {
    if (!p.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const c = live.current
      if (e.key.startsWith('Arrow') || e.key === 'Tab') { typed.current = false; return }
      if (/^[0-9]$/.test(e.key)) { typed.current = true; c.add(e.key) }
      else if (e.key === 'Backspace' && c.pin) c.erase()
      else if (e.key === 'Enter' && typed.current) void c.submit()
      else return
      e.preventDefault(); e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [p.open])

  const prompt = p.confirm && first !== null ? 'Enter it once more to confirm.' : p.hint
  return (
    <Modal open={p.open} onClose={p.onClose} title={p.title} firstFocusKey={HOME} testId="pin-dialog">
      {prompt && <p className="muted">{prompt}</p>}
      <div className="pin-dots" role="status" aria-live="polite" aria-label={pin.length ? `${pin.length} digits entered` : 'No digits entered'} data-testid="pin-dots">
        {pin.length ? Array.from(pin, (_, i) => <i key={i} />) : <span className="label">{MIN} to {MAX} digits</span>}
      </div>
      {locked
        ? <p role="alert" data-testid="pin-locked" style={{ color: 'var(--t-danger)' }}>Too many wrong PINs. Try again in {clock(left)}.</p>
        : error && <p role="alert" data-testid="pin-error" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      {/* A boundary, and no memory: arrows never leave the keypad, and coming back into it lands on 5. */}
      <FocusGroup focusKey="pin:pad" className="pin-pad" role="group" label="Keypad" boundary remember={false} preferred={HOME}>
        {GRID.slice(0, 3).flat().map((k) => <PinKey key={k} k={k} label={k} locked={locked} onEnter={() => add(k)}>{k}</PinKey>)}
        <PinKey k="erase" icon="delete" label="Erase last digit" locked={locked} inactive={!pin} onEnter={erase} />
        <PinKey k="0" label="0" locked={locked} onEnter={() => add('0')}>0</PinKey>
        <PinKey k="ok" primary icon="check" label={p.submitLabel ?? 'OK'} locked={locked} inactive={!ready} onEnter={() => { void setFocus(HOME); void submit() }} testId="pin-ok" />
      </FocusGroup>
      <p className="muted pin-private" data-testid="pin-private">Your place on the keypad is hidden. Each digit starts from 5.</p>
      <div><Button focusKey="pin:cancel" onEnter={p.onClose}>Cancel</Button></div>
    </Modal>
  )
}
