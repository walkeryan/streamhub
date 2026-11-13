import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { FocusGroup, Focusable, type FocusableHandle } from '../nav/Focusable'
import { useBackHandler, useOverlayFocus } from '../nav/back'
import { usePhone } from '../lib/layout'
import { Icon } from './Icon'
import { Modal } from './ui'

export interface ChipMenuOption { id: string; label: string; count?: number }

const GAP = 6 // px between the chip and its list
const EDGE = 8 // px the list keeps from the window's edges

/**
 * A chip that is its own drop-down: OK opens a short list under it, and the chip names the choice made there.
 * Opening chooses nothing, so Back (or a click anywhere else) leaves the choice as it was. On a phone the
 * list is the bottom sheet.
 */
export function ChipMenu({ focusKey, name, value, all, options, onChange, testId }: {
  focusKey: string
  /** What the chip stands for: its text while `all` is the choice, and the list's name. */
  name: string
  /** The id in force. The chip is pressed while it is `all` or one of `options`, and names the option after `name`. */
  value: string
  /** The first entry: everything the chip stands for. */
  all: ChipMenuOption
  options: ChipMenuOption[]; onChange: (id: string) => void; testId?: string
}) {
  const phone = usePhone()
  const [open, setOpen] = useState(false)
  const chip = useRef<FocusableHandle>(null)
  const entries = [all, ...options]
  const chosen = entries.find((o) => o.id === value)
  const detail = chosen && chosen.id !== all.id ? chosen.label : null
  const close = () => setOpen(false)
  const first = `${focusKey}:option:${(chosen ?? all).id}`
  const list = (
    <FocusGroup focusKey={`${focusKey}:options`} boundary className="chip-menu-list" role="menu" label={name}>
      {entries.map((o) => (
        <Focusable key={o.id} focusKey={`${focusKey}:option:${o.id}`} className="chip-menu-item fx-none" role="menuitemradio" aria={{ checked: o.id === value }}
          label={o.count === undefined ? o.label : `${o.label} (${o.count.toLocaleString()})`} onEnter={() => { onChange(o.id); close() }}>
          <Icon name="check" size={16} />
          <span className="truncate">{o.label}</span>
          {o.count !== undefined && <span className="label">{o.count.toLocaleString()}</span>}
        </Focusable>
      ))}
    </FocusGroup>
  )
  return <>
    <Focusable ref={chip} focusKey={focusKey} className="chip fx-flat" aria-pressed={!!chosen} label={detail ? `${name} · ${detail}` : name} data-testid={testId}
      aria={{ haspopup: phone ? 'dialog' : 'menu', expanded: open }} onEnter={() => setOpen(true)}>
      {name}{detail && <span className="chip-menu-choice"> · {detail}</span>}<Icon name="down" size={14} />
    </Focusable>
    {phone ? <Modal open={open} onClose={close} title={name} firstFocusKey={first} variant="sheet">{list}</Modal>
      : open && <Popover anchor={chip} firstFocusKey={first} onClose={close}>{list}</Popover>}
  </>
}

/**
 * The list under its chip. It is drawn over the page, not inside the row: a row of chips scrolls sideways
 * and clips what overflows it. Placed from the chip's box: above it when there is no room below, moved left
 * when it would cross the window's right edge, and scrolling inside itself when it is longer than the room it has.
 * Like a dialog it traps Back and the arrows, and puts focus back on the chip when it closes.
 */
function Popover({ anchor, firstFocusKey, onClose, children }: { anchor: RefObject<FocusableHandle | null>; firstFocusKey: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  useBackHandler(() => { onClose() })
  useOverlayFocus(true, firstFocusKey)
  useLayoutEffect(() => {
    const place = () => {
      const menu = box.current, chip = anchor.current?.el
      if (!menu || !chip) return
      const at = chip.getBoundingClientRect(), scrolled = menu.scrollTop
      menu.style.minWidth = `${at.width}px`
      menu.style.maxHeight = ''
      const width = menu.offsetWidth, height = menu.offsetHeight
      const below = window.innerHeight - EDGE - (at.bottom + GAP), above = at.top - GAP - EDGE
      const down = height <= below || below >= above
      const room = Math.max(0, down ? below : above)
      if (height > room) menu.style.maxHeight = `${room}px`
      menu.style.top = `${down ? at.bottom + GAP : at.top - GAP - Math.min(height, room)}px`
      menu.style.left = `${Math.max(EDGE, Math.min(at.left, window.innerWidth - EDGE - width))}px`
      menu.scrollTop = scrolled
    }
    place()
    window.addEventListener('resize', place)
    document.body.classList.add('no-scroll')
    return () => { window.removeEventListener('resize', place); document.body.classList.remove('no-scroll') }
  }, [anchor])
  return createPortal(
    <div className="chip-menu-scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} className="chip-menu">{children}</div>
    </div>,
    document.body,
  )
}
