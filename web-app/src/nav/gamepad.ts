/**
 * A game controller (an Xbox's, or one plugged into a computer) drives the app with the keys a TV
 * remote sends: D-pad or left stick for the arrows, A for Enter, B for Back. Spatial navigation and
 * the Back stack already answer those keys, so the controller needs nothing of its own past this file.
 *
 * Edge on Xbox gives a page a mouse pointer by default; `navigator.gamepadInputEmulation = 'gamepad'`
 * opts out so the Gamepad API sees the controller. The Android TV app is left alone: Android turns a
 * controller into D-pad key presses itself, and reading the pad here as well would move focus twice.
 */
import { inShell } from '../shell'
import { heldKeys, padStep, type PadKey, type PadSnapshot } from './gamepadKeys'

function snapshot(pad: Gamepad): PadSnapshot {
  return { pressed: pad.buttons.map((b) => b.pressed), axes: pad.axes }
}

function connectedPads(): Gamepad[] {
  return [...navigator.getGamepads()].filter((p): p is Gamepad => !!p && p.connected && p.mapping === 'standard')
}

/** The key goes where a real one would: the focused control, bubbling up to the window's listeners. */
function press(type: 'keydown' | 'keyup', key: PadKey): void {
  const target = document.activeElement instanceof HTMLElement ? document.activeElement : document.body
  target.dispatchEvent(new KeyboardEvent(type, { key, code: key, bubbles: true, cancelable: true }))
}

let started = false

export function startGamepadNavigation(): void {
  if (started || inShell() || typeof navigator.getGamepads !== 'function') return
  started = true
  const nav = navigator as Navigator & { gamepadInputEmulation?: string }
  if ('gamepadInputEmulation' in nav) nav.gamepadInputEmulation = 'gamepad'

  const repeatAt = new Map<PadKey, number>()
  let frame = 0
  const poll = () => {
    const pads = connectedPads()
    const { down, up } = padStep(repeatAt, heldKeys(pads.map(snapshot)), performance.now())
    for (const key of up) press('keyup', key)
    for (const key of down) press('keydown', key)
    // Polling stops with the last controller and starts again when one connects.
    frame = pads.length ? requestAnimationFrame(poll) : 0
  }
  const start = () => { if (!frame) frame = requestAnimationFrame(poll) }
  window.addEventListener('gamepadconnected', start)
  // A controller already in use before the page loaded is listed without a connected event.
  if (connectedPads().length) start()
}
