import type { Ref } from 'react'
import { Focusable, type FocusableHandle } from '../nav/Focusable'
import { cn } from '../lib/util'
import { Icon, type IconName } from './icons'

export type GlassSize = 'sm' | 'md' | 'lg'

/**
 * A round glass button with no words: the secondary actions of a screen (Start over, My List, ⋯ More, the
 * player's transport). Its `label` is what assistive tech reads; the same words (or `tip`) show as a
 * small tooltip under it while it has focus (remote, keyboard) or the pointer. Phones never show the tip.
 *
 * - `size`: sm 2.3rem (bars, rows), md 2.9rem (a screen's action row, the default), lg 3.6rem.
 * - `solid`: the white disc of the player's play/pause; anywhere else, keep to glass.
 * - `tip={false}`: no visual tooltip (the player keeps its keyboard hints in `title`).
 * - The glass is drawn from `--glass-ink` (the theme's text colour; white over a picture: put the button
 *   inside `.ds-on-media`, `.player` or `.mini-bar`).
 */
export function GlassIconButton(p: {
  focusKey: string; icon: IconName; label: string; onEnter: () => void
  tip?: string | false; tipAt?: 'below' | 'above'; size?: GlassSize; solid?: boolean
  pressed?: boolean; disabled?: boolean; testId?: string; title?: string; className?: string
  /** Filled glyph while pressed (My List's bookmark, Favorite's heart). */
  fillWhenPressed?: boolean
  /** A button that opens a menu or a sheet: says so to assistive tech. */
  expanded?: boolean; popup?: 'menu' | 'dialog'
  buttonRef?: Ref<FocusableHandle>; scroll?: boolean
}) {
  const button = (
    <Focusable ref={p.buttonRef} focusKey={p.focusKey} onEnter={p.onEnter} disabled={p.disabled} label={p.label} title={p.title}
      aria-pressed={p.pressed} data-testid={p.testId} scroll={p.scroll}
      aria={p.expanded === undefined ? undefined : { haspopup: p.popup ?? 'menu', expanded: p.expanded }}
      className={cn('ds-glass', p.size && p.size !== 'md' && `ds-glass-${p.size}`, p.solid && 'ds-glass-solid', p.tip === false && p.className)}>
      <Icon name={p.icon} filled={p.fillWhenPressed && p.pressed ? true : undefined} />
    </Focusable>
  )
  if (p.tip === false) return button
  return (
    <span className={cn('ds-glass-wrap', p.className)} data-tip={p.tipAt ?? 'below'}>
      {button}
      <span className="ds-glass-tip" aria-hidden="true">{p.tip ?? p.label}</span>
    </span>
  )
}
