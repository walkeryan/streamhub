import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode } from 'react'
import type { Tile } from '../api/types'
import { useMediaActions } from '../api/mediaActions'
import { OverflowMenu, type OverflowItem, type OverflowMenuHandle } from '../ds/OverflowMenu'
import { HOLD_MS, swallowEnterUntilRelease } from '../nav/hold'
import { Button, Modal } from './ui'

/** A finger that moves this far is scrolling, not holding. */
const SLOP_PX = 10

/**
 * A card and the menu that belongs to it. The arrows only ever move between cards: the menu opens by
 * holding OK (the card then acts when OK comes up, so the two can be told apart), by the Menu key, by
 * pressing and holding the card with a finger, or from the corner button a pointer finds on hover.
 */
export function CardActions({ className, cardKey, onMenu, children }: {
  className: string; cardKey: string; onMenu: () => void; children: ReactNode
}) {
  const hold = useRef<number | null>(null)
  const cancel = () => {
    if (hold.current === null) return
    window.clearTimeout(hold.current)
    hold.current = null
  }
  // A finger: down and still for as long as a held OK. The click that follows the lift is not a tap.
  const press = useRef<{ timer: number; x: number; y: number } | null>(null)
  const held = useRef(false)
  const lift = () => {
    if (!press.current) return
    window.clearTimeout(press.current.timer)
    press.current = null
  }
  useEffect(() => () => { cancel(); lift() }, [])
  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    held.current = false
    if (event.pointerType !== 'touch' || !card(event)) return
    lift()
    const timer = window.setTimeout(() => { press.current = null; held.current = true; onMenu() }, HOLD_MS)
    press.current = { timer, x: event.clientX, y: event.clientY }
  }
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (press.current && Math.hypot(event.clientX - press.current.x, event.clientY - press.current.y) > SLOP_PX) lift()
  }
  const click = (event: MouseEvent<HTMLDivElement>) => {
    if (!held.current) return
    held.current = false
    event.preventDefault()
    event.stopPropagation()
  }
  // A finger's long press is this menu, not the browser's own for a link. A mouse keeps its right click.
  const contextMenu = (event: MouseEvent<HTMLDivElement>) => { if (press.current || held.current) event.preventDefault() }
  const card = (event: { target: EventTarget }) => {
    const el = (event.target as HTMLElement).closest<HTMLElement>('[data-focus-key]')
    return el?.getAttribute('data-focus-key') === cardKey ? el : null
  }
  const down = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !card(event)) return
    if (event.key === 'ContextMenu') {
      event.preventDefault()
      event.stopPropagation()
      onMenu()
    }
    if (event.key !== 'Enter') return
    // Kept from the spatial navigation, which would open the card on the way down.
    event.preventDefault()
    event.stopPropagation()
    if (event.repeat || hold.current !== null) return
    hold.current = window.setTimeout(() => {
      hold.current = null
      swallowEnterUntilRelease()
      onMenu()
    }, HOLD_MS)
  }
  const up = (event: KeyboardEvent<HTMLDivElement>) => {
    // Only a press that began on this card opens it: an OK that came down on another screen does not.
    if (event.key !== 'Enter' || hold.current === null) return
    cancel()
    event.stopPropagation()
    card(event)?.click()
  }
  return <div className={className} onKeyDownCapture={down} onKeyUpCapture={up} onBlurCapture={cancel}
    onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={lift} onPointerCancel={lift}
    onClickCapture={click} onContextMenu={contextMenu}>{children}</div>
}

/** One menu beside one real content link. Media state loads only when the viewer opens this menu. */
export function MediaCardActions({ tile, className, cardKey, menuKey, items = [], children }: {
  tile: Tile; className?: string; cardKey: string; menuKey: string; items?: OverflowItem[]; children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const menu = useRef<OverflowMenuHandle>(null)
  const actions = useMediaActions(tile, open)
  // The first entry is where focus lands, so it changes nothing: it does what pressing the card does.
  const openCard = () => document.querySelector<HTMLElement>(`[data-focus-key="${CSS.escape(cardKey)}"]`)?.click()
  const first: OverflowItem = tile.kind === 'channel'
    ? { key: 'open', icon: 'play', label: 'Watch', onEnter: openCard }
    : { key: 'open', icon: 'chevR', label: 'Open', onEnter: openCard }
  const mediaItems: OverflowItem[] = actions.ready ? [
    { key: 'save', icon: 'bookmark', label: actions.saved ? 'Remove from My List' : 'Add to My List', pressed: actions.saved, fillWhenPressed: true,
      keepOpen: true, disabled: actions.busy, group: 1, onEnter: () => void actions.setSaved(!actions.saved) },
    { key: 'rate-up', icon: 'thumbUp' as const, label: actions.vote === 'up' ? 'Remove thumbs up' : 'Thumbs up', pressed: actions.vote === 'up',
      keepOpen: true, disabled: actions.busy, group: 1, onEnter: () => void actions.rate(actions.vote === 'up' ? null : 'up') },
    { key: 'rate-down', icon: 'thumbDown' as const, label: actions.vote === 'down' ? 'Remove thumbs down' : 'Thumbs down', pressed: actions.vote === 'down',
      keepOpen: true, disabled: actions.busy, group: 1, onEnter: () => void actions.rate(actions.vote === 'down' ? null : 'down') },
    ...(actions.canRemove ? [{ key: 'remove-library', icon: 'trash' as const, label: 'Remove from library', danger: true, group: 2, disabled: actions.busy, onEnter: () => setConfirmRemove(true) }] : []),
  ] : actions.error
    ? [{ key: 'retry-actions', icon: 'refresh', label: 'Retry loading actions', keepOpen: true, group: 1, onEnter: actions.retry }]
    : [{ key: 'loading', icon: 'refresh', label: 'Loading actions…', disabled: true, group: 1, onEnter: () => undefined }]
  const remove = async () => {
    await actions.remove()
    setConfirmRemove(false)
  }
  const status = actions.busy || (open && !actions.ready && !actions.error) ? 'busy' : open && actions.error ? 'error' : undefined
  return <>
    <CardActions className={`media-card ${className ?? ''}`} cardKey={cardKey} onMenu={() => menu.current?.open()}>
      {children}
      <div className="media-card-menu">
        <OverflowMenu focusKey={`${menuKey}:button`} menuKey={menuKey} title={tile.title} label={`More actions for ${tile.title}`} tip="More" status={status} size="sm"
          testId="media-card-actions" items={[first, ...items, ...mediaItems]} onOpenChange={setOpen} controls={menu} />
      </div>
    </CardActions>
    <Modal open={confirmRemove} onClose={() => setConfirmRemove(false)} title={tile.title}
      firstFocusKey={`${menuKey}:remove-cancel`} variant="sheet" testId="remove-library-confirm">
      <p>Remove from the household library?</p>
      <div className="lib-actions">
        <Button focusKey={`${menuKey}:remove-cancel`} ghost disabled={actions.busy} onEnter={() => setConfirmRemove(false)}>Cancel</Button>
        <Button focusKey={`${menuKey}:remove-confirm`} danger disabled={actions.busy} onEnter={() => void remove()}>Remove</Button>
      </div>
    </Modal>
  </>
}
