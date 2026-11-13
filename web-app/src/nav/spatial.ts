import { init, setFocus, getCurrentFocusKey, doesFocusableExist } from '@noriginmedia/norigin-spatial-navigation-core'

let started = false

function preserveBrowserHistoryShortcut(event: KeyboardEvent): void {
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey &&
      (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
    // Norigin's window listener treats modified arrows like D-pad input and
    // prevents their default action. Stop app handlers, but leave Chrome's
    // native Back/Forward action intact (including while an input is focused).
    event.stopImmediatePropagation()
  }
}

/** Leaf controls carry their focus key (see Focusable); a control inside a hidden parent has no box. */
function tabStops(): HTMLElement[] {
  // An open dialog keeps Tab to itself, the last one opened on top.
  const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]')
  const scope: ParentNode = dialogs.length ? dialogs[dialogs.length - 1]! : document
  return [...scope.querySelectorAll<HTMLElement>('[data-focus-key]')]
    .filter((el) => el.getAttribute('aria-disabled') !== 'true' && el.getClientRects().length > 0)
}

/**
 * Tab and Shift+Tab walk the controls in page order. Every control is tabindex -1 so that the
 * remote's arrows, not the browser, decide where focus goes; without this a keyboard user
 * pressing Tab in a text box left the page instead of reaching the next box.
 */
function tabThroughControls(event: KeyboardEvent): void {
  if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return
  const stops = tabStops()
  if (!stops.length) return
  const at = stops.findIndex((el) => el.dataset.focused === 'true' || el.contains(document.activeElement))
  const step = event.shiftKey ? -1 : 1
  const next = stops[at < 0 ? (step > 0 ? 0 : stops.length - 1) : (at + step + stops.length) % stops.length]!
  event.preventDefault()
  event.stopImmediatePropagation()
  setFocus(next.dataset.focusKey!)
}

export function startSpatialNavigation(): void {
  if (started) return
  started = true
  window.addEventListener('keydown', preserveBrowserHistoryShortcut, true)
  window.addEventListener('keydown', tabThroughControls, true)
  window.addEventListener('keyup', preserveBrowserHistoryShortcut, true)
  init({
    shouldFocusDOMNode: true,
    domNodeFocusOptions: { preventScroll: true },
    throttle: 0,
    throttleKeypresses: false,
  })
}

export { setFocus, getCurrentFocusKey, doesFocusableExist }
export {
  pause as pauseNav, resume as resumeNav, navigateByDirection,
} from '@noriginmedia/norigin-spatial-navigation-core'
