import { useImperativeHandle, useRef, useState, type Ref } from 'react'
import { FocusGroup, Focusable, type FocusableHandle } from '../nav/Focusable'
import { Popover } from '../components/ChipMenu'
import { Button, Modal } from '../components/ui'
import { usePhone } from '../lib/layout'
import { GlassIconButton, type GlassSize } from './GlassIconButton'
import { Icon, type IconName } from './icons'

export interface OverflowItem {
  key: string; icon: IconName; label: string; onEnter: () => void
  testId?: string; pressed?: boolean; disabled?: boolean
  /** Items are grouped in the order given; a rule separates one group from the next. */
  group?: number
  /** Toggles in place (Favorite): the menu stays open and then names what it has become. */
  keepOpen?: boolean
  /** Filled glyph while pressed (the heart). */
  fillWhenPressed?: boolean
  /** Removes or deletes: drawn in the danger colour. Keep these in a last group of their own. */
  danger?: boolean
}

/** For an owner that opens the menu another way than its button (a card: holding OK, the Menu key). */
export interface OverflowMenuHandle { open: () => void }

/**
 * The ⋯ button and the seldom-used actions behind it. A short list under the button on a TV or a desktop
 * (drawn over the page, placed from the button, Back closes it and puts focus back on the button); a sheet
 * from the bottom edge, with Close, on a phone. Focus keys: the button is `focusKey`, the list
 * `${menuKey}`, each item `${menuKey}:${item.key}`. Renders nothing when there are no items.
 */
export function OverflowMenu({ focusKey, menuKey, items, title, label = 'More actions', tip = 'More', icon = 'dots', status, testId, menuTestId, size, onOpenChange, controls }: {
  focusKey: string; menuKey: string; items: OverflowItem[]
  /** The sheet's heading on a phone (usually the title's name). */
  title: string
  label?: string; tip?: string; icon?: IconName; status?: 'active' | 'pending' | 'busy' | 'error'; testId?: string; menuTestId?: string; size?: GlassSize; onOpenChange?: (open: boolean) => void
  controls?: Ref<OverflowMenuHandle>
}) {
  const phone = usePhone()
  const [open, setOpen] = useState(false)
  const button = useRef<FocusableHandle>(null)
  const changeOpen = (next: boolean) => { setOpen(next); onOpenChange?.(next) }
  useImperativeHandle(controls, () => ({ open: () => changeOpen(true) }))
  if (!items.length) return null
  const run = (item: OverflowItem) => {
    if (item.keepOpen) return item.onEnter()
    changeOpen(false)
    item.onEnter()
  }
  const first = `${menuKey}:${(items.find(item => !item.disabled) ?? items[0]!).key}`
  const list = (
    <FocusGroup focusKey={menuKey} boundary className="ds-menu-list" role="menu" label={label} data-testid={menuTestId}>
      {items.map((item, i) => <span key={item.key} style={{ display: 'contents' }}>
        {i > 0 && (item.group ?? 0) !== (items[i - 1]!.group ?? 0) && <hr className="ds-menu-sep" />}
        <Focusable focusKey={`${menuKey}:${item.key}`} className="ds-menu-item" data={{ danger: item.danger || undefined }} role="menuitem" label={item.label} aria-pressed={item.pressed} disabled={item.disabled}
          data-testid={item.testId} onEnter={() => run(item)}>
          <Icon name={item.icon} filled={item.fillWhenPressed && item.pressed ? true : undefined} /><span>{item.label}</span>
        </Focusable>
      </span>)}
      {phone && <Button focusKey={`${menuKey}:close`} ghost onEnter={() => changeOpen(false)}>Close</Button>}
    </FocusGroup>
  )
  return <>
    <GlassIconButton focusKey={focusKey} icon={icon} status={status} label={label} tip={tip} size={size} buttonRef={button} expanded={open} popup={phone ? 'dialog' : 'menu'}
      onEnter={() => changeOpen(true)} testId={testId} />
    {phone ? <Modal open={open} onClose={() => changeOpen(false)} title={title} firstFocusKey={first} variant="sheet">{list}</Modal>
      : open && <Popover anchor={button} firstFocusKey={first} onClose={() => changeOpen(false)} className="ds-menu">{list}</Popover>}
  </>
}
