import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { rowWindow, type RowWindow } from '../lib/guideWindow'

export const VIEW_BUFFER = 4 // rows drawn beyond each edge of the view

export const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize)

/**
 * A box of rows that scrolls inside itself (the guide's grid, the Channels list): which rows are on screen,
 * and putting the box where it belongs. Every row is `rowRem` tall, so the rows in view follow from the
 * scroll offset alone. Scrolling changes state only when the view crosses a row, so a frame of scroll that
 * stays inside one costs no render.
 */
export function useRowView({ filterKey: key, count, rowRem, headRem = 0, row, morePages, onPlace, onMeasure }: {
  /** The filters in force: the box is put in place once for each. */
  filterKey: string; count: number
  rowRem: number
  /** What stays put over the top of the rows (the guide's hours). */
  headRem?: number
  /** The row at the top of the view where Back left it; null opens on the first. */
  row: number | null
  /** Rows past `count` can still be loaded: a view further down than that waits for them. */
  morePages: boolean
  /** The rest of putting the box in place (the guide's hour). Called once for each `filterKey`. */
  onPlace?: (box: HTMLDivElement) => void
  /** The view as the viewer has it now, to its first row: called when the box scrolls or changes size, once it is in place. Keep it stable. */
  onMeasure: (box: HTMLDivElement, topRow: number) => void
}) {
  const scroller = useRef<HTMLDivElement | null>(null)
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const boxRef = useCallback((el: HTMLDivElement | null) => { scroller.current = el; setBox(el) }, [])
  const [win, setWin] = useState<RowWindow>({ first: 0, end: 0 })
  // The filters the box was last scrolled into place for: null until it has been, after Back too.
  const [placed, setPlaced] = useState<string | null>(null)

  const measure = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const px = remPx(), rowPx = rowRem * px
    const next = rowWindow(el.scrollTop, el.clientHeight - headRem * px, rowPx, count, VIEW_BUFFER)
    setWin((w) => (w.first === next.first && w.end === next.end ? w : next))
    // Until the box is in place its scroll offsets are not the viewer's: they must not replace what Back restores.
    // (A hundredth of a row's allowance: an offset set from a row reads back a hair short of it.)
    if (placed === key) onMeasure(el, Math.floor(el.scrollTop / rowPx + 0.01))
  }, [count, placed, key, rowRem, headRem, onMeasure])
  const latest = useRef(measure)
  useEffect(() => { latest.current = measure }, [measure])
  const frame = useRef(0)
  const onScroll = useCallback(() => { if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; latest.current() }) }, [])
  // The box's own size changes with the window, and with the header above it (the search field, a details line).
  useEffect(() => {
    if (!box) return
    const watch = new ResizeObserver(() => latest.current())
    watch.observe(box)
    return () => { watch.disconnect(); cancelAnimationFrame(frame.current); frame.current = 0 }
  }, [box])
  // Put the box where it belongs once it is drawn: at the first row, or where Back left it.
  useLayoutEffect(() => {
    // (While other filters load, `box` is still the element that has just left the page: nothing to place.)
    if (!box || !box.isConnected || !count || placed === key) return
    // Back to a row further down than is loaded: its page is asked for (useChannelRows), and the box waits for it.
    if (row !== null && row >= count && morePages) return
    box.scrollTop = (row ?? 0) * rowRem * remPx()
    onPlace?.(box)
    setPlaced(key)
  }, [box, placed, key, row, count, morePages, rowRem, onPlace])
  useLayoutEffect(() => { measure() }, [measure, box])

  // Focus that the pointer moved does not scroll the box: resting it on a half-shown row would move the rows under it.
  const pointer = useRef(false)
  useEffect(() => {
    const byKey = () => { pointer.current = false }
    window.addEventListener('keydown', byKey, true)
    return () => window.removeEventListener('keydown', byKey, true)
  }, [])
  const onPointerMove = useCallback(() => { pointer.current = true }, [])
  /**
   * Bring a row focused by the keyboard out from under what covers the top of the box, or up from its
   * bottom edge, `clearRem` clear of either (room for a focus ring the box would clip). The box runs to the
   * bottom of the window; on a TV the focused row still stops short of the overscan margin.
   */
  const revealRow = useCallback((node: HTMLElement, clearRem = 0) => {
    const el = scroller.current
    if (!el) return
    const px = remPx(), at = el.getBoundingClientRect(), r = node.getBoundingClientRect()
    const margin = window.matchMedia('(min-width: 1800px)').matches ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--safe-y')) * px : 0
    const top = at.top + (headRem + clearRem) * px, bottom = Math.min(at.top + el.clientHeight, window.innerHeight - margin) - clearRem * px
    if (r.top < top) el.scrollTop -= top - r.top
    else if (r.bottom > bottom) el.scrollTop += r.bottom - bottom
  }, [headRem])

  return {
    scroller, box, boxRef, onScroll, win,
    /** The box is drawn and where the viewer (or Back) put it. */
    placed: placed === key,
    /** Whether focus is being moved by the pointer, and the handler (in the capture phase, on the box) that says so. */
    pointer, onPointerMove, revealRow,
  }
}
