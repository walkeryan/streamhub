import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
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
      <p className="muted">Press the arrow keys or the buttons, {ARROWS_MIN} to {ARROWS_MAX} times. On a shared TV, picking this person then asks for the same arrows on the remote, with no keypad and no OK.</p>
      <p className="muted">Six arrows are much harder to guess than four.</p>
      {p.note && <p className="muted">{p.note}</p>}
      <div className="pin-dots arrow-entered" role="status" aria-live="polite" data-testid="arrow-entered"
        aria-label={arrows.length ? `${arrows.length} arrows: ${arrows.map((a) => NAME[a]).join(', ')}` : 'No arrows entered'}>
        {arrows.length ? arrows.map((a, i) => <span key={i} aria-hidden="true">{GLYPH[a]}</span>) : <span className="label">{ARROWS_MIN} to {ARROWS_MAX} arrows</span>}
      </div>
      {error && <p role="alert" data-testid="arrow-error" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      <div className="arrow-pad" role="group" aria-label="Arrows">
        {PAD.map((a, i) => a
          ? <Button key={a} focusKey={`arrows:${a}`} className="pin-key" label={NAME[a]} onEnter={() => add(a)} testId={`arrow-${a}`}>{GLYPH[a]}</Button>
          : <span key={i} aria-hidden="true" />)}
      </div>
      <p className="label">Typed as a PIN anywhere else, the pattern is its digits: ↑ 2, ← 4, → 6, ↓ 8.</p>
      <div className="flex gap-3 flex-wrap">
        <Button focusKey="arrows:save" primary disabled={!ready} onEnter={() => void save()} testId="arrow-save">Save pattern</Button>
        <Button focusKey="arrows:clear" disabled={!arrows.length || busy} onEnter={clear} testId="arrow-clear">Clear</Button>
        <Button focusKey="arrows:cancel" onEnter={p.onClose}>Cancel</Button>
      </div>
    </Modal>
  )
}
