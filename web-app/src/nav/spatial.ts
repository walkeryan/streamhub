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

export function startSpatialNavigation(): void {
  if (started) return
  started = true
  window.addEventListener('keydown', preserveBrowserHistoryShortcut, true)
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
