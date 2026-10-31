import { init, setFocus, getCurrentFocusKey, doesFocusableExist } from '@noriginmedia/norigin-spatial-navigation-core'

let started = false
export function startSpatialNavigation(): void {
  if (started) return
  started = true
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
