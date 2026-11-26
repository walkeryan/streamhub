import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import { lastControllerUse, onControllerUse } from '../nav/gamepad'
import { lsGet, lsSet } from '../lib/util'
import type { PadButton, PadHint } from '../player/shortcuts'

/**
 * On-screen help for a game controller: its buttons drawn as they look on an Xbox controller, a hint
 * the first time one is used, and the player's row of what the buttons do there (Player.tsx). Nothing
 * here shows to someone who has not touched a controller.
 */

/** A controller used within this long still counts as the one in hand: the player shows its hints. */
export const PAD_RECENT_MS = 10 * 60_000

const FACE = new Set<PadButton>(['A', 'B', 'X', 'Y'])
const SPOKEN: Partial<Record<PadButton, string>> = { LS: 'left stick', RS: 'right stick' }

/** One button: A, B, X and Y as their coloured circles, the rest as labelled pills. */
export function PadGlyph({ name }: { name: PadButton }) {
  return (
    <span className={FACE.has(name) ? 'pad-btn pad-face' : 'pad-btn'} data-btn={name} aria-label={SPOKEN[name] ?? name} role="img">
      {name === 'RS' ? 'R stick' : name === 'LS' ? 'L stick' : name}
    </span>
  )
}

export function PadGlyphs({ buttons }: { buttons: readonly PadButton[] }) {
  return <span className="pad-glyphs">{buttons.map((b) => <PadGlyph key={b} name={b} />)}</span>
}

/** True while a controller has been used within PAD_RECENT_MS; follows new presses as they come. */
export function useControllerRecent(): boolean {
  const [recent, setRecent] = useState(() => lastControllerUse() + PAD_RECENT_MS > Date.now())
  useEffect(() => {
    let timer = 0
    const arm = (at: number) => {
      window.clearTimeout(timer)
      const left = at + PAD_RECENT_MS - Date.now()
      setRecent(left > 0)
      if (left > 0) timer = window.setTimeout(() => setRecent(false), left)
    }
    arm(lastControllerUse())
    const off = onControllerUse(arm)
    return () => { off(); window.clearTimeout(timer) }
  }, [])
  return recent
}

/** The player's row: what the buttons do here and now (shortcuts.ts padHints decides). */
export function PadHintRow({ hints }: { hints: readonly PadHint[] }) {
  return (
    <div className="pad-hints" role="group" data-testid="pad-hints" aria-label="Controller buttons">
      {hints.map((hint) => (
        <span key={hint.buttons.join('/')} className="pad-hint"><PadGlyphs buttons={hint.buttons} /><span>{hint.does}</span></span>
      ))}
    </div>
  )
}

const SEEN_KEY = 'sh.controller.hinted'
/** Long enough to read twice; the hint takes no focus, so it must go by itself. */
const HINT_MS = 12_000

/**
 * The first time a controller is used in this browser: a short note of the main buttons, once. It takes
 * no focus (the controller keeps driving the screen beneath it) and goes by itself, or with its Close.
 */
export function ControllerHint() {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (lsGet(SEEN_KEY)) return
    return onControllerUse(() => {
      if (lsGet(SEEN_KEY)) return
      lsSet(SEEN_KEY, '1')
      setShown(true)
    })
  }, [])
  useEffect(() => {
    if (!shown) return
    const timer = window.setTimeout(() => setShown(false), HINT_MS)
    return () => window.clearTimeout(timer)
  }, [shown])
  if (!shown) return null
  return (
    <div className="pad-intro" role="status" aria-live="polite" data-testid="controller-hint">
      <div className="pad-intro-body">
        <strong>Controller connected</strong>
        <span className="pad-intro-keys">
          <span><PadGlyph name="A" /> Select</span>
          <span><PadGlyph name="B" /> Back</span>
          <span><PadGlyph name="X" /> Play/pause</span>
          <span><PadGlyphs buttons={['LB', 'RB']} /> Channel or skip</span>
          <span><PadGlyph name="Start" /> Player menu</span>
        </span>
        <span className="label">All the buttons: Start in the player, then Controls and shortcuts.</span>
      </div>
      <button type="button" className="pad-intro-close" aria-label="Close" onClick={() => setShown(false)}><Icon name="x" size={18} /></button>
    </div>
  )
}
