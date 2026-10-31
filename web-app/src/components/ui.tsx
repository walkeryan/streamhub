import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useBackHandler, useOverlayFocus } from '../nav/back'
import { Icon } from './Icon'
import { hueOf, cn, reducedMotion } from '../lib/util'
import { useUi } from '../store/ui'
import type { SourceBadge, Tile } from '../api/types'

export function Button(p: {
  focusKey?: string; icon?: string; children?: ReactNode; onEnter?: () => void; primary?: boolean; ghost?: boolean; danger?: boolean
  disabled?: boolean; pressed?: boolean; label?: string; className?: string; testId?: string; onFocus?: () => void; onBlur?: () => void
  iconOnly?: boolean; scroll?: boolean
}) {
  return (
    <Focusable
      focusKey={p.focusKey}
      onEnter={p.onEnter}
      disabled={p.disabled}
      label={p.label}
      aria-pressed={p.pressed}
      data-testid={p.testId}
      scroll={p.scroll}
      onFocus={p.onFocus}
      onBlur={p.onBlur}
      className={cn('btn', p.primary && 'btn-primary', p.ghost && 'btn-ghost', p.danger && 'btn-danger', p.iconOnly && 'btn-icon', p.className)}
    >
      {p.icon && <Icon name={p.icon} size={20} />}
      {p.children}
    </Focusable>
  )
}

export function Chip(p: { focusKey?: string; children: ReactNode; pressed?: boolean; onEnter: () => void; testId?: string }) {
  return (
    <Focusable focusKey={p.focusKey} onEnter={p.onEnter} aria-pressed={p.pressed} data-testid={p.testId} className="chip fx-flat">
      {p.children}
    </Focusable>
  )
}

export function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className={cn('bar', className)} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <i style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  )
}

export function SourceChips({ sources, max = 2 }: { sources: SourceBadge[]; max?: number }) {
  const seen = new Set<string>()
  const uniq = sources.filter((s) => (seen.has(s.name) ? false : (seen.add(s.name), true)))
  const inApp = uniq.filter((s) => s.playable_in_app)
  const hand = uniq.filter((s) => !s.playable_in_app)
  const shown = [...inApp, ...hand].slice(0, max)
  return (
    <>
      {shown.map((s) => (
        <span key={s.source_id + s.name} className={cn('badge', s.playable_in_app && 'badge-play')}>
          {s.playable_in_app ? 'Play' : 'Open'} · {s.name}
        </span>
      ))}
    </>
  )
}

export function Meta({ items }: { items: Array<string | number | null | undefined | false> }) {
  const list = items.filter((x): x is string | number => x !== null && x !== undefined && x !== false && x !== '')
  return (
    <p className="muted flex flex-wrap items-center" style={{ gap: '0.25rem 0.875rem' }}>
      {list.map((x, i) => (
        <span key={i} style={i ? { paddingLeft: '0.875rem', borderLeft: '1px solid color-mix(in srgb, var(--t-text) 28%, transparent)' } : undefined}>{x}</span>
      ))}
    </p>
  )
}

export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={cn('skel', className)} style={style} aria-hidden="true" />
}

export function Empty({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 py-12 px-1" role="status">
      <h2 className="title-xl">{title}</h2>
      {hint && <p className="muted max-w-prose">{hint}</p>}
      {children}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : 'Something went wrong.'
  return (
    <div className="flex flex-col items-start gap-3 py-12" role="alert">
      <h2 className="title-xl">Could not load this screen</h2>
      <p className="muted">{msg}</p>
      {onRetry && <Button focusKey="retry" primary icon="refresh" onEnter={onRetry}>Try again</Button>}
    </div>
  )
}

export function Attribution({ text = 'Availability data by JustWatch' }: { text?: string }) {
  return <p className="label" data-testid="justwatch-attribution">{text}</p>
}

let modalSeq = 0
/** Modal dialog: traps Back, restores focus to the opener on close. Children should contain Focusables. */
export function Modal(p: { open: boolean; onClose: () => void; title: string; children: ReactNode; firstFocusKey: string; wide?: boolean }) {
  const [id] = useState(() => `modal-${++modalSeq}`)
  useBackHandler(() => { p.onClose() }, p.open)
  useOverlayFocus(p.open, p.firstFocusKey)
  useEffect(() => {
    if (!p.open) return
    document.body.classList.add('no-scroll')
    return () => document.body.classList.remove('no-scroll')
  }, [p.open])
  if (!p.open) return null
  return createPortal(
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && p.onClose()}>
      <FocusGroup focusKey={id} boundary className="dialog" role="dialog" label={p.title} style={p.wide ? { width: 'min(46rem,100%)' } : undefined}>
        <h2 className="title-xl">{p.title}</h2>
        {p.children}
      </FocusGroup>
    </div>,
    document.body,
  )
}

export function Toasts() {
  const toasts = useUi((s) => s.toasts)
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast" data-tone={t.tone} role="status">{t.text}</div>
      ))}
    </div>
  )
}

/** Crossfading backdrop (hero / title detail). */
export function Crossfade({ src, className }: { src: string | null | undefined; className?: string }) {
  const [layers, setLayers] = useState<{ src: string | null | undefined; key: number }[]>([{ src, key: 0 }])
  const seq = useRef(0)
  useEffect(() => {
    setLayers((l) => (l[l.length - 1]?.src === src ? l : [...l.slice(-1), { src, key: ++seq.current }]))
    const t = window.setTimeout(() => setLayers((l) => l.slice(-1)), reducedMotion() ? 20 : 600)
    return () => window.clearTimeout(t)
  }, [src])
  const last = layers[layers.length - 1]?.key
  return (
    <>
      {layers.map((l) => (
        <div key={l.key} className={cn('hero-layer', className)} data-on={l.key === last}
          style={l.src ? { backgroundImage: `url("${l.src}")` } : { background: 'var(--t-surface)' }} />
      ))}
    </>
  )
}

export function LogoBlock({ name, logo }: { name: string; logo?: string | null }) {
  const h = hueOf(name)
  return (
    <div className="logo-block" style={{ background: `linear-gradient(135deg, hsl(${h} 45% 32%), hsl(${(h + 40) % 360} 50% 22%))` }}>
      {logo ? <img src={logo} alt="" loading="lazy" /> : name.slice(0, 3).toUpperCase()}
    </div>
  )
}

export function tileHasArt(t: Tile) {
  return !!(t.poster || t.backdrop)
}
