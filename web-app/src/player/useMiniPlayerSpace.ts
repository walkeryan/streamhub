import { useEffect } from 'react'

/** While a mini player bar shows, the page keeps room for it at the bottom (base.css: html[data-mini-player]). */
export function useMiniPlayerSpace(shown: boolean): void {
  useEffect(() => {
    if (!shown) return
    const root = document.documentElement
    root.dataset.miniPlayer = 'true'
    return () => { delete root.dataset.miniPlayer }
  }, [shown])
}
