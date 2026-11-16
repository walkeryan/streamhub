/**
 * A game controller (an Xbox's, or one plugged into a computer) drives the app with the keys a TV
 * remote and a keyboard send: D-pad or left stick for the arrows, A for Enter, B for Back, and the
 * player's keys for the rest (gamepadKeys.ts has the whole map). Spatial navigation, the Back stack
 * and the player already answer those keys, so the controller needs nothing of its own past this
 * file, save the right stick: where no screen takes its volume keys, it scrolls the page.
 *
 * Edge on Xbox gives a page a mouse pointer by default; `navigator.gamepadInputEmulation = 'gamepad'`
 * opts out so the Gamepad API sees the controller. The Android TV app is left alone: Android turns a
 * controller into D-pad key presses itself, and reading the pad here as well would move focus twice.
 */
import { inShell } from '../shell'
import { SCROLL_KEYS, heldKeys, keyEventInit, padStep, type PadKey, type PadSnapshot } from './gamepadKeys'

function snapshot(pad: Gamepad): PadSnapshot {
  return { pressed: pad.buttons.map((b) => b.pressed), values: pad.buttons.map((b) => b.value), axes: pad.axes }
}

function connectedPads(): Gamepad[] {
  return [...navigator.getGamepads()].filter((p): p is Gamepad => !!p && p.connected && p.mapping === 'standard')
}

/**
 * The key goes where a real one would: the focused control, bubbling up to the window's listeners.
 * False when a listener took it (prevented its default).
 */
function press(type: 'keydown' | 'keyup', key: PadKey): boolean {
  const target = document.activeElement instanceof HTMLElement ? document.activeElement : document.body
  const init = keyEventInit(key)
  return target.dispatchEvent(new KeyboardEvent(type, { ...init, code: init.key, bubbles: true, cancelable: true }))
}

/** One right-stick step scrolls this share of what is in view: about two screens a second while held. */
const SCROLL_SHARE = 0.25

/** The box the focused control scrolls in (a dialog, a list), else the page. */
function scroller(): Element {
  for (let el = document.activeElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    const { overflowY } = getComputedStyle(el)
    if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1) return el
  }
  return document.scrollingElement ?? document.documentElement
}

function scrollBy(dir: -1 | 1): void {
  const box = scroller()
  const page = box === document.scrollingElement || box === document.documentElement
  box.scrollBy({ top: dir * Math.round((page ? window.innerHeight : box.clientHeight) * SCROLL_SHARE) })
}

// ---- when a controller was last used: the hints (PadHints.tsx) show only to someone holding one ----
let lastUse = 0
let lastTold = 0
const listeners = new Set<(at: number) => void>()
/** Listeners hear a held button at most this often, not at every repeat. */
const TELL_EVERY_MS = 5000

/** When a controller button last went down (Date.now()), 0 if never on this page. */
export function lastControllerUse(): number { return lastUse }

/** Called when a controller is used: at once the first time, then at most every few seconds. */
export function onControllerUse(listener: (at: number) => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function used(): void {
  lastUse = Date.now()
  if (lastUse - lastTold < TELL_EVERY_MS) return
  lastTold = lastUse
  for (const listener of [...listeners]) listener(lastUse)
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
    if (down.length) used()
    for (const key of down) {
      const taken = !press('keydown', key)
      const scroll = SCROLL_KEYS.get(key)
      if (scroll && !taken) scrollBy(scroll)
    }
    // Polling stops with the last controller and starts again when one connects.
    frame = pads.length ? requestAnimationFrame(poll) : 0
  }
  const start = () => { if (!frame) frame = requestAnimationFrame(poll) }
  window.addEventListener('gamepadconnected', start)
  // A controller already in use before the page loaded is listed without a connected event.
  if (connectedPads().length) start()
}
