import { useSyncExternalStore } from 'react'

// Same breakpoints as base.css. Below PHONE the bottom bar replaces the top bar; from WIDE up the
// Live TV header has room beside it for the guide's programme details.
const PHONE = '(max-width: 720px)'
const WIDE = '(min-width: 1100px)'
const watch = (media: string) => (notify: () => void) => {
  const query = window.matchMedia(media)
  query.addEventListener('change', notify)
  return () => query.removeEventListener('change', notify)
}
const watchPhone = watch(PHONE), watchWide = watch(WIDE)

/** Phone layout. Screens use it to leave out controls a phone has no room for, not merely hide them:
 *  a hidden control would still take remote focus. */
export const usePhone = (): boolean => useSyncExternalStore(watchPhone, () => window.matchMedia(PHONE).matches)

/** Wide layout: likewise, the details panel is left out below it, and a one-line summary takes its place. */
export const useWide = (): boolean => useSyncExternalStore(watchWide, () => window.matchMedia(WIDE).matches)
