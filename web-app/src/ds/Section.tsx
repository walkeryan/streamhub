import type { ReactNode } from 'react'
import { FocusGroup } from '../nav/Focusable'
import { cn } from '../lib/util'

/**
 * A heading for a block of a screen: the name, then a quiet count or note beside it, then (optional) an
 * action at the far end. Every row and section of the app uses this one, so headings line up and share
 * one size. `as="h1"` for the screen's own title (see `PageHeader`).
 */
export function SectionHeader({ title, meta, action, className, as = 'h2' }: {
  title: ReactNode; meta?: ReactNode; action?: ReactNode; className?: string; as?: 'h1' | 'h2' | 'h3'
}) {
  const H = as
  return (
    <div className={cn('ds-section-head', className)}>
      <H className={as === 'h1' ? 'ds-page-title' : 'ds-section-title'}>{title}{meta != null && meta !== false && <small>{meta}</small>}</H>
      {action && <div className="ds-section-action">{action}</div>}
    </div>
  )
}

/** A screen's title line: the name in the page-title size, then the screen's own tools at the end. */
export function PageHeader({ title, meta, children, className }: { title: ReactNode; meta?: ReactNode; children?: ReactNode; className?: string }) {
  return <SectionHeader as="h1" title={title} meta={meta} action={children} className={cn('ds-page-head', className)} />
}

/** A section: a header over its content. `label` names it for assistive tech when the title is not plain text. */
export function Section({ title, meta, action, label, testId, className, children, data }: {
  title?: ReactNode; meta?: ReactNode; action?: ReactNode; label?: string; testId?: string; className?: string; children: ReactNode
  data?: Record<string, string | undefined>
}) {
  const dataAttrs = Object.fromEntries(Object.entries(data ?? {}).map(([k, v]) => [`data-${k}`, v]))
  return (
    <section className={cn('ds-section', className)} aria-label={label ?? (typeof title === 'string' ? title : undefined)} data-testid={testId} {...dataAttrs}>
      {title != null && <SectionHeader title={title} meta={meta} action={action} />}
      {children}
    </section>
  )
}

/**
 * A row that scrolls sideways out to the window's edges (inside the page's side padding, it bleeds by
 * `--page-x` so the first tile lines up with the heading and the last scrolls to the edge). The focus ring
 * of a tile is never clipped: the row is padded by what a ring needs. Remote: Left/Right along it,
 * Up/Down to the rows around it; it remembers the tile last focused.
 */
export function HRow({ focusKey, children, className, remember = true, preferred, label, testId }: {
  focusKey: string; children: ReactNode; className?: string; remember?: boolean; preferred?: string; label?: string; testId?: string
}) {
  return <FocusGroup focusKey={focusKey} className={cn('ds-row', className)} remember={remember} preferred={preferred} label={label} data-testid={testId}>{children}</FocusGroup>
}
