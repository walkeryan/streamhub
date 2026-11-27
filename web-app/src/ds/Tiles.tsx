import type { ReactNode } from 'react'
import { Focusable } from '../nav/Focusable'
import { SafeImg } from '../components/SafeImg'
import { ProgressBar } from '../components/ui'
import { cn, hueOf } from '../lib/util'
import { Icon } from './icons'

/** The colour a picture-less card falls back to: from its name, so a title keeps its colour everywhere. */
export const nameGradient = (name: string) => {
  const h = hueOf(name)
  return `linear-gradient(135deg, hsl(${h} 62% 56%), hsl(${(h + 30) % 360} 58% 38%))`
}

/** Watched: a green tick in a dark disc, top right of a picture. */
export function WatchedTick({ label }: { label?: string }) {
  return <span className="ds-tick" role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}><Icon name="check" /></span>
}

/** A folded artwork corner marks titles that can play in StreamHub. */
export function AvailabilityCorner() {
  return <span className="availability-corner" role="img" aria-label="Available to watch" data-testid="tile-available" />
}

/**
 * A landscape still with its words under it: an episode in a season strip, a title in Continue Watching.
 * The picture carries the progress bar along its bottom and the watched tick in its corner; `current`
 * outlines it in the accent (the episode this page is about). `meta` follows the title, quieter
 * ("· 50 min", "S2 · E3"). Width comes from `--still-w` (rows set it; default 11.8rem, 13rem on a phone).
 */
export function StillCard(p: {
  focusKey: string; href?: string; label: string; onEnter: () => void; onFocus?: () => void
  image?: string | null; fallbackName: string; title: ReactNode; meta?: ReactNode
  progressPct?: number | null; watched?: boolean; available?: boolean; current?: boolean
  /** Over the picture's top-left corner: a status chip ("On now", a request's phase). */
  badge?: ReactNode
  /** Over the picture's top-right corner, in place of the watched tick. */
  corner?: ReactNode
  testId?: string; className?: string; role?: string
}) {
  const pct = p.progressPct ?? 0
  return (
    <Focusable focusKey={p.focusKey} href={p.href} className={cn('ds-still', p.className)} role={p.role ?? 'link'} aria-current={p.current ? 'page' : undefined}
      data-testid={p.testId} label={p.label} onEnter={p.onEnter} onFocus={p.onFocus ? () => p.onFocus!() : undefined}>
      <div className="ds-still-art">
        <SafeImg className="art" src={p.image} alt="" loading="lazy" draggable={false}
          fallback={<span className="ds-art-fallback" style={{ background: nameGradient(p.fallbackName) }}>{p.fallbackName}</span>} />
        {p.badge && <span className="ds-still-badge">{p.badge}</span>}
        {pct > 0 && pct < 100 && <ProgressBar label={`Viewing progress · ${p.label}`} pct={pct} className="ds-still-bar" />}
        {p.corner ?? (p.watched && <WatchedTick />)}
        {p.available && <AvailabilityCorner />}
      </div>
      <p className="ds-still-cap">{p.title}{p.meta ? <small>{p.meta}</small> : null}</p>
    </Focusable>
  )
}

/** A poster (or any tile) with its name under it rather than over it: More like this, grids of results. */
export function Captioned({ children, caption, className }: { children: ReactNode; caption: ReactNode; className?: string }) {
  return (
    <div className={cn('ds-captioned', className)}>
      {children}
      <p className="ds-cap-below">{caption}</p>
    </div>
  )
}
