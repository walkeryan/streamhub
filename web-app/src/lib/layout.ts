import { useSyncExternalStore } from 'react'

// Same breakpoint as base.css: below it the bottom bar replaces the top bar.
const PHONE = '(max-width: 720px)'
const subscribe = (notify: () => void) => {
  const query = window.matchMedia(PHONE)
  query.addEventListener('change', notify)
  return () => query.removeEventListener('change', notify)
}

/** Phone layout. Screens use it to leave out controls a phone has no room for, not merely hide them:
 *  a hidden control would still take remote focus. */
export const usePhone = (): boolean => useSyncExternalStore(subscribe, () => window.matchMedia(PHONE).matches)
