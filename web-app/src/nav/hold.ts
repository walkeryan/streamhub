/**
 * Holding OK (a remote's centre key, Enter, a controller's A) on a card opens its menu, as it does on
 * every TV home screen. The card acts on release so that a hold can be told from a press
 * (components/CardActions.tsx). The menu opens under a key that is still down: until that key comes
 * up, its repeats must not press whatever the menu put under focus.
 */
export const HOLD_MS = 550
/** A held key repeats many times a second: this long without one means it came up and the keyup was lost. */
const RELEASE_AFTER_MS = 1000

let swallowing = false
let release: number | undefined

function stop(): void {
  swallowing = false
  window.clearTimeout(release)
}

function onKeyDown(event: KeyboardEvent): void {
  if (!swallowing || event.key !== 'Enter') return
  event.preventDefault()
  event.stopImmediatePropagation()
  window.clearTimeout(release)
  release = window.setTimeout(stop, RELEASE_AFTER_MS)
}

function onKeyUp(event: KeyboardEvent): void {
  if (!swallowing || event.key !== 'Enter') return
  event.stopImmediatePropagation()
  stop()
}

/** Call once, before the spatial navigation's own listeners are added. */
export function watchHeldEnter(): void {
  window.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('keyup', onKeyUp, true)
}

/** A hold has just acted: ignore OK until it is released. */
export function swallowEnterUntilRelease(): void {
  swallowing = true
  window.clearTimeout(release)
  release = window.setTimeout(stop, RELEASE_AFTER_MS)
}
