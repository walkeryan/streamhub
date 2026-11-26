import type { KeyboardEvent, ReactNode } from 'react'
import { setFocus } from '../nav/spatial'

/** The corner button is inside the poster bounds, so geometric navigation skips it. */
export function CardActions({ className, cardKey, menuKey, children }: {
  className: string; cardKey: string; menuKey: string; children: ReactNode
}) {
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = (event.target as HTMLElement).closest('[data-focus-key]')?.getAttribute('data-focus-key')
    const next = current === cardKey && event.key === 'ArrowUp' ? menuKey
      : current === menuKey && event.key === 'ArrowDown' ? cardKey : null
    if (!next) return
    event.preventDefault()
    event.stopPropagation()
    void setFocus(next)
  }
  return <div className={className} onKeyDownCapture={move}>{children}</div>
}
