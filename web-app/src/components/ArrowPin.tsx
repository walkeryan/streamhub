import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { explain } from './PinPad'
import { Button, Modal } from './ui'

export type Arrow = 'up' | 'left' | 'right' | 'down'
/**
 * Where each arrow sits on a phone keypad around 5. An arrow pattern is stored as these digits, so
 * it is an ordinary PIN to the server and can still be typed as one wherever a PIN is asked for.
 */
export const ARROW_DIGIT: Record<Arrow, string> = { up: '2', left: '4', right: '6', down: '8' }
const GLYPH: Record<Arrow, string> = { up: '↑', left: '←', right: '→', down: '↓' }
const NAME: Record<Arrow, string> = { up: 'Up', left: 'Left', right: 'Right', down: 'Down' }
const KEY: Record<string, Arrow> = { ArrowUp: 'up', ArrowLeft: 'left', ArrowRight: 'right', ArrowDown: 'down' }
export const ARROWS_MIN = 4, ARROWS_MAX = 8
export const arrowDigits = (arrows: readonly Arrow[]) => arrows.map((a) => ARROW_DIGIT[a]).join('')

/**
 * The four on-screen arrows, laid out as they are on a remote. They never turn disabled: one that
 * holds focus must keep it (a full pattern or a pending save just ignores the press).
 */
const PAD: (Arrow | null)[] = [null, 'up', null, 'left', null, 'right', null, 'down', null]

/**
 * Choosing a PIN as a pattern of 4 to 8 D-pad arrows, for people who would rather press arrows on
 * the TV remote than walk a number pad. Entered with the arrow keys or the buttons, and shown as it
 * is entered: this is where it is chosen, not where it is asked for. `onSubmit` gets the keypad
 * digits (see ARROW_DIGIT) and resolves when the server took them; when it throws, the dialog stays
 * open and says why.
 *
 * While it is open the arrow keys belong to the pattern, so they never move focus: Backspace takes
 * the last arrow back, Enter saves, Escape cancels.
 */
export function ArrowPinDialog(p: {
  open: boolean; title: string; note?: string
  onSubmit: (digits: string) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const [arrows, setArrows] = useState<Arrow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // A fresh dialog each time it opens.
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) { setArrows([]); setError(null); setBusy(false) } }

  const ready = arrows.length >= ARROWS_MIN && !busy
  const add = (a: Arrow) => { if (!busy) { setError(null); setArrows((v) => (v.length < ARROWS_MAX ? [...v, a] : v)) } }
  const erase = () => setArrows((v) => v.slice(0, -1))
  const clear = () => { setArrows([]); setError(null) }
  const save = async () => {
    if (busy) return
    if (arrows.length < ARROWS_MIN) { setError(`Enter at least ${ARROWS_MIN} arrows.`); return }
    setBusy(true); setError(null)
    try { await p.onSubmit(arrowDigits(arrows)) } catch (e) {
      const code = e instanceof ApiError ? e.code : undefined
      setError(p.explain?.(code)
        ?? (e instanceof ApiError && e.status === 429 ? 'Too many wrong PINs. Try again later.' : e instanceof Error ? e.message : 'That did not work. Try again.'))
    } finally { setBusy(false) }
  }

  // Captured ahead of the spatial navigation, which would otherwise move focus on every arrow.
  const live = useRef({ add, erase, save })
  live.current = { add, erase, save }
  useEffect(() => {
    if (!p.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const c = live.current
      const arrow = KEY[e.key]
      if (arrow) c.add(arrow)
      else if (e.key === 'Backspace') c.erase()
      else if (e.key === 'Enter') void c.save()
      else return
      e.preventDefault(); e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [p.open])

  return (
    <Modal open={p.open} onClose={p.onClose} title={p.title} firstFocusKey="arrows:up" testId="arrow-pin-dialog">
      <p className="acct-note">Press the arrow keys or the buttons, {ARROWS_MIN} to {ARROWS_MAX} times. On "Who's watching?", picking this person then asks for the same arrows on the remote or keyboard, with no keypad and no OK.</p>
      <p className="acct-small">Six arrows are much harder to guess than four.</p>
      {p.note && <p className="acct-note">{p.note}</p>}
      <div className="pin-dots arrow-entered" role="status" aria-live="polite" data-testid="arrow-entered"
        aria-label={arrows.length ? `${arrows.length} arrows: ${arrows.map((a) => NAME[a]).join(', ')}` : 'No arrows entered'}>
        {arrows.length ? arrows.map((a, i) => <span key={i} aria-hidden="true">{GLYPH[a]}</span>) : <span className="label">{ARROWS_MIN} to {ARROWS_MAX} arrows</span>}
      </div>
      {error && <p role="alert" data-testid="arrow-error" className="acct-error">{error}</p>}
      <div className="arrow-pad" role="group" aria-label="Arrows">
        {PAD.map((a, i) => a
          ? <Button key={a} focusKey={`arrows:${a}`} className="pin-key" label={NAME[a]} onEnter={() => add(a)} testId={`arrow-${a}`}>{GLYPH[a]}</Button>
          : <span key={i} aria-hidden="true" />)}
      </div>
      <p className="acct-small">Typed as a PIN anywhere else, the pattern is its digits: ↑ 2, ← 4, → 6, ↓ 8.</p>
      <div className="acct-actions">
        <Button focusKey="arrows:save" primary disabled={!ready} onEnter={() => void save()} testId="arrow-save">Save pattern</Button>
        <Button focusKey="arrows:clear" disabled={!arrows.length || busy} onEnter={clear} testId="arrow-clear">Clear</Button>
        <Button focusKey="arrows:cancel" ghost onEnter={p.onClose}>Cancel</Button>
      </div>
    </Modal>
  )
}

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

/**
 * Asking for an arrow pattern chosen before: `length` empty dots, one filling per arrow, and the last
 * one sends the pattern (`onSubmit` gets its keypad digits, see ARROW_DIGIT) with no OK step. This is
 * how a TV remote enters it: its D-pad arrives as the arrow keys, which are captured ahead of the
 * spatial navigation so they never move focus to the cards behind the dialog. A held key's repeats
 * are ignored, or one long press would fill every dot. Enter (the remote's OK) starts again and never
 * sends; Escape or Back closes. A wrong pattern empties the dots and says so; a 429 locks the entry
 * for the server's Retry-After. Which arrows were pressed is never shown. The on-screen arrows are
 * for a mouse or a touch screen.
 */
export function ArrowPinAskDialog(p: {
  open: boolean; title: string; hint?: string; length: number
  onSubmit: (digits: string) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const [entered, setEntered] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const left = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const locked = left > 0
  // The pattern so far, read and written synchronously: two presses inside one render must not both
  // see the same length, or the last dot could fill without sending (or send twice).
  const pattern = useRef<Arrow[]>([])
  // A fresh dialog each time it opens.
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) { setEntered(0); setError(null); setBusy(false) } }
  useEffect(() => { if (p.open) pattern.current = [] }, [p.open])

  useEffect(() => {
    if (!locked) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [locked])

  const clear = () => { pattern.current = []; setEntered(0) }
  const send = async (digits: string) => {
    setBusy(true); setError(null)
    try { await p.onSubmit(digits) } catch (e) {
      clear()
      setError(explain(e, p.explain, 'pattern'))
      if (e instanceof ApiError && e.status === 429) { setNow(Date.now()); setLockedUntil(Date.now() + (e.retryAfter ?? 60) * 1000) }
    } finally { setBusy(false) }
  }
  const add = (a: Arrow) => {
    if (busy || locked || pattern.current.length >= p.length) return
    setError(null)
    pattern.current = [...pattern.current, a]
    setEntered(pattern.current.length)
    if (pattern.current.length === p.length) void send(arrowDigits(pattern.current))
  }

  const live = useRef({ add, clear })
  live.current = { add, clear }
  useEffect(() => {
    if (!p.open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const arrow = KEY[e.key]
      if (arrow) { if (!e.repeat) live.current.add(arrow) }
      else if (e.key === 'Enter') live.current.clear()
      // Everything else, Escape and the platform Back included, goes on to the app.
      else return
      e.preventDefault(); e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [p.open])

  const status = locked ? `Too many wrong patterns. Try again in ${clock(left)}.` : busy ? 'Checking…' : error
  return (
    <Modal open={p.open} onClose={p.onClose} title={p.title} firstFocusKey="arrowask:cancel" testId="arrow-ask-dialog">
      {/* Centred, as the TV draws it: what is asked, a disc per arrow filling in the accent, then how. */}
      <div className="arrow-ask">
        {p.hint && <p className="acct-note">{p.hint}</p>}
        <div className="pin-dots arrow-dots" role="status" aria-live="polite" data-testid="arrow-dots" data-entered={entered}
          aria-label={`${entered} of ${p.length} arrows entered`}>
          {Array.from({ length: p.length }, (_, i) => <i key={i} data-filled={i < entered} />)}
        </div>
        {status
          ? <p role="alert" data-testid={locked ? 'arrow-locked' : 'arrow-error'} className={busy ? 'acct-small' : 'acct-error'}>{status}</p>
          : <p className="acct-small">Press {GLYPH.up} {GLYPH.left} {GLYPH.right} {GLYPH.down} {p.length} times. OK starts again, Back cancels.</p>}
        <div className="arrow-pad" role="group" aria-label="Arrows">
          {PAD.map((a, i) => a
            ? <Button key={a} focusKey={`arrowask:${a}`} className="pin-key" label={NAME[a]} onEnter={() => add(a)} testId={`arrow-ask-${a}`}>{GLYPH[a]}</Button>
            : <span key={i} aria-hidden="true" />)}
        </div>
        <div><Button focusKey="arrowask:cancel" onEnter={p.onClose}>Cancel</Button></div>
      </div>
    </Modal>
  )
}
