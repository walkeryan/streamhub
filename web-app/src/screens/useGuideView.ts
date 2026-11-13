import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { SLOT_MS, rowWindow, type RowWindow, type TimeSpan } from '../lib/guideWindow'
import { HEAD_REM, REM_PER_MIN, ROW_REM } from './GuideRow'

/** Where the view is, to the row and the half hour: what Back restores, and what decides which requests are due. */
export interface GuideView { row: number; from: number; to: number }
export const VIEW_BUFFER = 4 // rows drawn beyond each edge of the view

export const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize)
export const startOfSlot = (time: number) => Math.floor(time / SLOT_MS) * SLOT_MS
/** A length of time as a scroll offset. */
const offsetPx = (ms: number) => Math.max(0, (ms / 60_000) * REM_PER_MIN * remPx())

/**
 * The guide's scrolling box: which rows and hours are on screen, and putting the box where it belongs.
 * Scrolling changes state only when the view crosses a row or a half hour, so a frame of scroll that
 * stays inside both costs no render.
 */
export function useGuideView({ filterKey: key, count, span, view, setView, morePages }: {
  /** The filters in force: the grid is put in place once for each. */
  filterKey: string; count: number; span: TimeSpan
  view: GuideView | null; setView: Dispatch<SetStateAction<GuideView | null>>
  /** Rows past `count` can still be loaded: a view further down than that waits for them. */
  morePages: boolean
}) {
  const scroller = useRef<HTMLDivElement | null>(null)
  const [grid, setGrid] = useState<HTMLDivElement | null>(null)
  const gridRef = useCallback((el: HTMLDivElement | null) => { scroller.current = el; setGrid(el) }, [])
  const [win, setWin] = useState<RowWindow>({ first: 0, end: 0 })
  // The filters the grid was last scrolled into place for: null until it has been, after Back too.
  const [placed, setPlaced] = useState<string | null>(null)
  const [jump, setJump] = useState<number | null>(null)

  const measure = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const px = remPx(), minute = REM_PER_MIN * px, row = ROW_REM * px
    const next = rowWindow(el.scrollTop, el.clientHeight - HEAD_REM * px, row, count, VIEW_BUFFER)
    setWin((w) => (w.first === next.first && w.end === next.end ? w : next))
    // Until the grid is in place its scroll offsets are not the viewer's: they must not replace what Back restores.
    if (placed !== key) return
    const columnPx = parseFloat(getComputedStyle(el).getPropertyValue('--epg-ch')) * px
    // A minute's and a hundredth of a row's allowance: an offset set from a time or a row reads back a hair short of it.
    const from = startOfSlot(span.from + (el.scrollLeft / minute + 1) * 60_000)
    const to = Math.ceil((span.from + ((el.scrollLeft + el.clientWidth - columnPx) / minute - 1) * 60_000) / SLOT_MS) * SLOT_MS
    const top = Math.floor(el.scrollTop / row + 0.01)
    setView((v) => (v && v.row === top && v.from === from && v.to === to ? v : { row: top, from, to }))
  }, [count, placed, key, span.from, setView])
  const latest = useRef(measure)
  useEffect(() => { latest.current = measure }, [measure])
  const frame = useRef(0)
  const onScroll = useCallback(() => { if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; latest.current() }) }, [])
  // The grid's own box changes with the window, and with the header above it (a row of sub-groups, the search field).
  useEffect(() => {
    if (!grid) return
    const watch = new ResizeObserver(() => latest.current())
    watch.observe(grid)
    return () => { watch.disconnect(); cancelAnimationFrame(frame.current); frame.current = 0 }
  }, [grid])
  // Put the grid where it belongs once it is drawn: on the half hour before the one now is in, or where Back left it.
  useLayoutEffect(() => {
    // (While other filters load, `grid` is still the element that has just left the page: nothing to place.)
    if (!grid || !grid.isConnected || !count || placed === key) return
    // Back to a row further down than is loaded: the guide asks for its page, and the grid waits for it.
    if (view && view.row >= count && morePages) return
    grid.scrollTop = (view?.row ?? 0) * ROW_REM * remPx()
    grid.scrollLeft = offsetPx((view?.from ?? startOfSlot(Date.now() - SLOT_MS)) - span.from)
    setPlaced(key)
  }, [grid, placed, key, view, count, morePages, span.from])
  useLayoutEffect(() => {
    if (grid && jump !== null) { grid.scrollLeft = offsetPx(jump - span.from); setJump(null) }
  }, [grid, jump, span.from])
  useLayoutEffect(() => { measure() }, [measure, grid])

  return {
    scroller, gridRef, onScroll, win,
    /** The grid is drawn and where the viewer (or Back) put it. */
    placed: placed === key,
    /** Scroll so that this time is at the channel column, once the cells of this render are placed. */
    jumpTo: setJump,
  }
}
