import type { CSSProperties, ReactNode } from 'react'
import { Skeleton } from '../components/ui'
import { cn } from '../lib/util'

/**
 * A row still loading: a heading bar over tiles of the row's shape. Shown where the row will be, so
 * nothing jumps when it arrives.
 */
export function SkeletonRow({ layout = 'poster', count = 7, title = true }: { layout?: 'poster' | 'landscape' | 'channel'; count?: number; title?: boolean }) {
  return (
    <div className="ds-section ds-skel-row" aria-hidden="true">
      {title && <Skeleton className="ds-skel-title" />}
      <div className="ds-row">{Array.from({ length: count }, (_, i) => <Skeleton key={i} className={`ds-skel-tile ds-skel-${layout}`} />)}</div>
    </div>
  )
}

/**
 * A frosted card: a group of settings, a notice, an activity entry. Glass is drawn from the theme's text
 * colour, so it reads on the page, over a backdrop and in a light theme alike. `tone="accent"` tints it.
 */
export function GlassPanel({ children, className, as = 'div', tone, style, testId, label }: {
  children: ReactNode; className?: string; as?: 'div' | 'section' | 'article' | 'aside'; tone?: 'accent' | 'danger'
  style?: CSSProperties; testId?: string; label?: string
}) {
  const Tag = as as 'div'
  return <Tag className={cn('ds-panel', tone && `ds-panel-${tone}`, className)} style={style} data-testid={testId} aria-label={label}>{children}</Tag>
}
