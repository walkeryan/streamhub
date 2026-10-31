import { useState, type CSSProperties, type ReactNode } from 'react'

/** Up to two initials ("Demo Sport" -> "DS", "TF1" -> "TF"); never empty. */
export function initialsOf(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}\s]/gu, ' ').trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return (words[0] ?? '?').slice(0, 2).toUpperCase()
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? '')).toUpperCase()
}

/** Generated placeholder: initials on a surface-coloured block. Fills its positioned parent. */
export function Initials({ name, className, style }: { name: string; className?: string; style?: CSSProperties }) {
  return (
    <div className={className} data-testid="img-fallback" aria-hidden="true"
      style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', background: 'var(--t-surface-alt)',
        color: 'var(--t-text)', fontWeight: 700, letterSpacing: '0.04em', ...style }}>
      {initialsOf(name)}
    </div>
  )
}

/**
 * <img> that never shows the browser's broken-image glyph: if `src` is empty or fails to load, `fallback` renders
 * instead. The error state is keyed to the URL, so a new src gets a fresh attempt.
 */
export function SafeImg({ src, fallback, ...img }: { src: string | null | undefined; fallback: ReactNode } &
  Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src' | 'onError'>) {
  const [failed, setFailed] = useState<string | null>(null)
  if (!src || failed === src) return <>{fallback}</>
  return <img {...img} src={src} onError={() => setFailed(src)} />
}
