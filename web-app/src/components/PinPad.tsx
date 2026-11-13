import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { Button, Modal } from './ui'

const MIN = 4, MAX = 8
const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9']
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
 * A PIN of four to eight digits, entered on a keypad or a keyboard and never shown. `onSubmit`
 * resolves when the PIN was accepted; when it throws, the dialog stays open and says why. A 429
 * locks the keypad for the server's Retry-After and counts it down. With `confirm`, a new PIN is
 * entered twice before it is submitted.
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
  // A fresh dialog each time it opens.
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) { setPin(''); setFirst(null); setError(null); setBusy(false) } }

  useEffect(() => {
    if (!locked) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [locked])
  // The keypad comes back under the remote once the lock ends.
  useEffect(() => { if (p.open && !locked && lockedUntil) void setFocus('pin:1') }, [locked, lockedUntil, p.open])

  const ready = pin.length >= MIN && !busy && !locked
  const submit = async () => {
    if (!ready) return
    if (p.confirm && first === null) { setFirst(pin); setPin(''); setError(null); void setFocus('pin:1'); return }
    if (p.confirm && first !== pin) { setFirst(null); setPin(''); setError('Those two PINs are not the same. Start again.'); void setFocus('pin:1'); return }
    setBusy(true); setError(null)
    try { await p.onSubmit(pin) } catch (e) {
      setPin(''); setFirst(null)
      setError(explain(e, p.explain))
      if (e instanceof ApiError && e.status === 429) { setNow(Date.now()); setLockedUntil(Date.now() + (e.retryAfter ?? 60) * 1000) } else void setFocus('pin:1')
    } finally { setBusy(false) }
  }
  const add = (digit: string) => { if (!busy && !locked) { setError(null); setPin((v) => (v.length < MAX ? v + digit : v)) } }
  const erase = () => setPin((v) => v.slice(0, -1))

  // A keyboard types the PIN wherever the remote's focus happens to be. Captured ahead of the app's own
  // key handling: Enter must submit rather than press the focused key, Backspace must erase rather than go Back.
  const live = useRef({ submit, add, erase, pin })
  live.current = { submit, add, erase, pin }
  useEffect(() => {
    if (!p.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const c = live.current
      if (/^[0-9]$/.test(e.key)) c.add(e.key)
      else if (e.key === 'Backspace' && c.pin) c.erase()
      else if (e.key === 'Enter' && c.pin.length >= MIN) void c.submit()
      else return
      e.preventDefault(); e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [p.open])

  const prompt = p.confirm && first !== null ? 'Enter it once more to confirm.' : p.hint
  return (
    <Modal open={p.open} onClose={p.onClose} title={p.title} firstFocusKey="pin:1" testId="pin-dialog">
      {prompt && <p className="muted">{prompt}</p>}
      <div className="pin-dots" role="status" aria-live="polite" aria-label={pin.length ? `${pin.length} digits entered` : 'No digits entered'} data-testid="pin-dots">
        {pin.length ? Array.from(pin, (_, i) => <i key={i} />) : <span className="label">{MIN} to {MAX} digits</span>}
      </div>
      {locked
        ? <p role="alert" data-testid="pin-locked" style={{ color: 'var(--t-danger)' }}>Too many wrong PINs. Try again in {clock(left)}.</p>
        : error && <p role="alert" data-testid="pin-error" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      <FocusGroup focusKey="pin:pad" className="pin-pad" role="group" label="Keypad">
        {KEYS.map((k) => <Button key={k} focusKey={`pin:${k}`} className="pin-key" label={k} disabled={locked} onEnter={() => add(k)}>{k}</Button>)}
        <Button focusKey="pin:erase" className="pin-key" icon="delete" label="Erase last digit" disabled={locked || !pin} onEnter={erase} />
        <Button focusKey="pin:0" className="pin-key" label="0" disabled={locked} onEnter={() => add('0')}>0</Button>
        <Button focusKey="pin:ok" className="pin-key" primary icon="check" label={p.submitLabel ?? 'OK'} disabled={!ready} onEnter={() => void submit()} testId="pin-ok" />
      </FocusGroup>
      <div><Button focusKey="pin:cancel" onEnter={p.onClose}>Cancel</Button></div>
    </Modal>
  )
}
