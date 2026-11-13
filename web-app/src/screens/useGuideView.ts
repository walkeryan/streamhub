import { useCallback, useLayoutEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { SLOT_MS, type TimeSpan } from '../lib/guideWindow'
import { HEAD_REM, REM_PER_MIN, ROW_REM } from './GuideRow'
import { remPx, useRowView } from './useRowView'

/** Where the view is, to the row and the half hour: what Back restores, and what decides which requests are due. */
export interface GuideView { row: number; from: number; to: number }

export const startOfSlot = (time: number) => Math.floor(time / SLOT_MS) * SLOT_MS
/** A length of time as a scroll offset. */
const offsetPx = (ms: number) => Math.max(0, (ms / 60_000) * REM_PER_MIN * remPx())

/**
 * The guide's scrolling box: the rows of useRowView, and the hours across them. Scrolling changes state only
 * when the view crosses a row or a half hour, so a frame of scroll that stays inside both costs no render.
 */
export function useGuideView({ filterKey, count, span, view, setView, morePages }: {
  /** The filters in force: the grid is put in place once for each. */
  filterKey: string; count: number; span: TimeSpan
  view: GuideView | null; setView: Dispatch<SetStateAction<GuideView | null>>
  /** Rows past `count` can still be loaded: a view further down than that waits for them. */
  morePages: boolean
}) {
  const [jump, setJump] = useState<number | null>(null)
  // The grid opens on the half hour before the one now is in, or on the hour Back left it at.
  const hour = view?.from ?? null
  const onPlace = useCallback((grid: HTMLDivElement) => { grid.scrollLeft = offsetPx((hour ?? startOfSlot(Date.now() - SLOT_MS)) - span.from) }, [hour, span.from])
  const onMeasure = useCallback((grid: HTMLDivElement, top: number) => {
    const px = remPx(), minute = REM_PER_MIN * px
    const columnPx = parseFloat(getComputedStyle(grid).getPropertyValue('--epg-ch')) * px
    // A minute's allowance: an offset set from a time reads back a hair short of it.
    const from = startOfSlot(span.from + (grid.scrollLeft / minute + 1) * 60_000)
    const to = Math.ceil((span.from + ((grid.scrollLeft + grid.clientWidth - columnPx) / minute - 1) * 60_000) / SLOT_MS) * SLOT_MS
    setView((v) => (v && v.row === top && v.from === from && v.to === to ? v : { row: top, from, to }))
  }, [span.from, setView])
  const rows = useRowView({ filterKey, count, rowRem: ROW_REM, headRem: HEAD_REM, row: view?.row ?? null, morePages, onPlace, onMeasure })
  const { box: grid } = rows
  useLayoutEffect(() => {
    if (grid && jump !== null) { grid.scrollLeft = offsetPx(jump - span.from); setJump(null) }
  }, [grid, jump, span.from])

  return {
    ...rows, gridRef: rows.boxRef,
    /** Scroll so that this time is at the channel column, once the cells of this render are placed. */
    jumpTo: setJump,
  }
}
