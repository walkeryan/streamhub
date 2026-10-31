import { useEffect } from 'react'
import { defaultThemeId, themes } from '../tokens'
import { applyAccent, dominantColor } from './adaptive'
import { useSession } from '../store/session'
import { lsGet, lsSet } from './util'

export function setTheme(id: string): void {
  const known = themes.some((t) => t.id === id) ? id : defaultThemeId
  document.documentElement.dataset.theme = known
  lsSet('sh.theme', known)
  if (known !== 'adaptive') applyAccent(null)
  const meta = document.querySelector('meta[name="theme-color"]')
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--t-bg').trim()
  if (meta && bg) meta.setAttribute('content', bg)
}

export function useTheme(themeId: string | undefined): void {
  useEffect(() => { setTheme(themeId ?? lsGet('sh.theme') ?? defaultThemeId) }, [themeId])
}

/** Pre-login: apply last used theme immediately to avoid a flash. */
export function bootTheme(): void {
  const t = lsGet('sh.theme')
  if (t) document.documentElement.dataset.theme = t
}


/** Poster Adaptive theme: tint accent + glow from the artwork currently in focus. No-op for other themes. */
export function useAdaptiveAccent(src: string | null | undefined): void {
  const themeId = useSession((s) => s.profile?.theme_id)
  useEffect(() => {
    if (themeId !== 'adaptive' || !src) return
    let live = true
    void dominantColor(src).then((c) => { if (live) applyAccent(c) })
    return () => { live = false }
  }, [themeId, src])
}
