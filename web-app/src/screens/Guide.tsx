import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useChannelPages, useEpgChunks, useRecordings, type ChannelFilters, type EpgRequest } from '../api/hooks'
import type { Channel, Programme } from '../api/types'
import { Button, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { useGo, useRestoreFocus } from '../nav/back'
import { useUi } from '../store/ui'
import { useHistoryState } from '../nav/historyState'
import { fmtTime, useNow } from '../lib/util'
import { useCan } from '../api/me'
import { recordingOf, recordingStateLabel } from '../lib/recordings'
import { SLOT_MS, extendedEnd, nearEnd, openingSpan, programmeAt, rowWindow, rowsToDraw, spanChunks, type RowWindow, type TimeSpan } from '../lib/guideWindow'
import { RecordDialog } from '../recordings/RecordDialog'
import { GuideDetailsLine, GuideDetailsPanel, type GuideDetailsProps } from './GuideDetails'
import { GuideRow, REM_PER_MIN, ROW_REM, COL_REM, cellKey, channelKey } from './GuideRow'

const HOUR_MS = 3600_000
const HEAD_REM = 2 // the time header, see .epg --epg-head
const BACK_H = 3
const FWD_H = 9
// Later hours are asked for as the viewer nears the end of what is loaded, up to a day ahead.
const EXTEND_H = 4
const HORIZON_H = 24
const PAGE = 40
const BUFFER = 4 // rows drawn beyond each edge of the view
const NEAR = 8 // rows from the end at which the next page of channels is asked for

/** Where the view is, to the row and the half hour: what Back restores, and what decides which requests are due. */
interface View { row: number; from: number; to: number }
const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize)
const startOfSlot = (time: number) => Math.floor(time / SLOT_MS) * SLOT_MS
/** A length of time as a scroll offset. */
const offsetPx = (ms: number) => Math.max(0, (ms / 60_000) * REM_PER_MIN * remPx())

export function Guide({ filters, scheduledOnly, onShowAll, selectedChannelId, onSelectChannel, wide, detailsSlot, upKey, onTotal }: {
  filters: ChannelFilters; scheduledOnly: boolean; onShowAll: () => void
  selectedChannelId: string | null; onSelectChannel: (id: string) => void
  /** Wide screens describe the focused programme in `detailsSlot`, beside the header; others in a line above the grid. */
  wide: boolean; detailsSlot: HTMLElement | null
  /** Where Up from the first row goes on a wide screen: the header's row of groups. */
  upKey: string
  onTotal: (total: number | null) => void
}) {
  const now = useNow(30_000)
  const [sel, setSel] = useHistoryState<Programme | null>('guide:selected-programme', null)
  const [span, setSpan] = useHistoryState<TimeSpan>('guide:time', openingSpan(Date.now(), BACK_H, FWD_H))
  const [view, setView] = useHistoryState<View | null>('guide:view', null)
  const key = JSON.stringify([filters, scheduledOnly])
  const [filterKey, setFilterKey] = useHistoryState('guide:filters', key)
  // Other filters start at the first channel; the hour on screen stays.
  if (filterKey !== key) { setFilterKey(key); setSel(null); setView((v) => v && { ...v, row: 0 }) }
  // The filters the grid was last scrolled into place for: null until it has been, after Back too.
  const [placed, setPlaced] = useState<string | null>(null)
  const [jump, setJump] = useState<number | null>(null)

  const ch = useChannelPages({ ...filters, has_epg: scheduledOnly || undefined }, PAGE)
  const pages = ch.data?.pages
  // A list that changed between two requests can repeat a channel: it keeps one row.
  const channels = useMemo(() => { const seen = new Set<string>(); return (pages ?? []).flatMap((p) => p.channels ?? []).filter((c) => !seen.has(c.id) && seen.add(c.id)) }, [pages])
  const count = channels.length
  const total = pages?.[0]?.total ?? count
  const rowOf = useMemo(() => new Map(channels.map((c, i) => [c.id, i])), [channels])
  const focusRow = (selectedChannelId !== null ? rowOf.get(selectedChannelId) : undefined) ?? null
  const [win, setWin] = useState<RowWindow>({ first: 0, end: 0 })
  const first = Math.min(win.first, Math.max(0, count - 1)), end = Math.min(Math.max(win.end, first + 1), count)
  const drawn = useMemo(() => rowsToDraw({ first, end }, focusRow, count), [first, end, focusRow, count])

  // Programmes: one request per page of channels and span of hours, made when its rows and hours come near the view.
  const requests = useMemo(() => {
    const out: EpgRequest[] = []
    let offset = 0
    for (const page of pages ?? []) {
      const ids = (page.channels ?? []).map((c) => c.id)
      const rowsDue = drawn.some((i) => i >= offset && i < offset + ids.length)
      for (const c of spanChunks(span, BACK_H + FWD_H, EXTEND_H)) out.push({ ids, ...c, enabled: rowsDue && (!view || (c.to > view.from - HOUR_MS && c.from < view.to + HOUR_MS)) })
      offset += ids.length
    }
    return out
  }, [pages, drawn, span, view])
  const epg = useEpgChunks(requests)
  const loadingIds = useMemo(() => new Set(requests.flatMap((r, i) => (epg.loading[i] ? r.ids : []))), [requests, epg.loading])

  const setChannelList = useUi((s) => s.setChannelList)
  const toast = useUi((s) => s.toast)
  const go = useGo()
  const [catchup, setCatchup] = useState<{ ch: Channel; p: Programme } | null>(null)
  const scroller = useRef<HTMLDivElement | null>(null)
  const [grid, setGrid] = useState<HTMLDivElement | null>(null)
  const gridRef = useCallback((el: HTMLDivElement | null) => { scroller.current = el; setGrid(el) }, [])
  // Recording: where the server has it on and this profile may schedule, OK on a programme that is on or still to come opens Record.
  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const [recording, setRecording] = useState<{ ch: Channel; id: string } | null>(null)
  const recordingProgramme = recording && epg.byChannel[recording.ch.id]?.find((x) => x.id === recording.id)

  useEffect(() => { if (pages) setChannelList(pages[0]?.list_id ?? null, channels) }, [pages, channels, setChannelList])
  useEffect(() => { onTotal(pages ? total : null) }, [pages, total, onTotal])

  /* ---------- the view: which rows and hours are on screen ---------- */
  const measure = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const px = remPx(), minute = REM_PER_MIN * px, row = ROW_REM * px
    const next = rowWindow(el.scrollTop, el.clientHeight - HEAD_REM * px, row, count, BUFFER)
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
  const onScroll = () => { if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; latest.current() }) }
  // The grid's own box changes with the window, and with the header above it (a row of sub-groups, the search field).
  useEffect(() => {
    if (!grid) return
    const watch = new ResizeObserver(() => latest.current())
    watch.observe(grid)
    return () => { watch.disconnect(); cancelAnimationFrame(frame.current); frame.current = 0 }
  }, [grid])
  // Put the grid where it belongs once it is drawn: on the half hour before the one now is in, or where Back left it.
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = ch
  useLayoutEffect(() => {
    // (While other filters load, `grid` is still the element that has just left the page: nothing to place.)
    if (!grid || !grid.isConnected || !count || placed === key) return
    // Back to a row further down than is loaded: its page is asked for below, and the grid waits for it.
    if (view && view.row >= count && hasNextPage) return
    grid.scrollTop = (view?.row ?? 0) * ROW_REM * remPx()
    grid.scrollLeft = offsetPx((view?.from ?? startOfSlot(Date.now() - SLOT_MS)) - span.from)
    setPlaced(key)
  }, [grid, placed, key, view, count, hasNextPage, span.from])
  useLayoutEffect(() => {
    if (grid && jump !== null) { grid.scrollLeft = offsetPx(jump - span.from); setJump(null) }
  }, [grid, jump, span.from])
  useLayoutEffect(() => { measure() }, [measure, grid])
  const ready = placed === key && count > 0

  /* ---------- loading as you go ---------- */
  const reachedRow = Math.max(end - 1 - BUFFER, focusRow ?? 0)
  const restoring = placed !== key && !!view && view.row >= count
  useEffect(() => {
    if (!pages || !hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    if (restoring || (ready && nearEnd(reachedRow, count, NEAR))) void fetchNextPage()
  }, [pages, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage, restoring, ready, reachedRow, count])
  const horizon = span.from + (BACK_H + HORIZON_H) * HOUR_MS
  const reachedTime = Math.max(view?.to ?? 0, sel ? Date.parse(sel.stop) : 0)
  useEffect(() => {
    const to = extendedEnd(span, reachedTime, EXTEND_H, horizon)
    if (ready && to !== span.to) setSpan({ from: span.from, to })
  }, [ready, span, reachedTime, horizon, setSpan])

  /* ---------- focus: arrows by programme and by time ---------- */
  const drawnOf = (c: Channel) => (epg.byChannel[c.id] ?? []).filter((p) => Date.parse(p.stop) > span.from && Date.parse(p.start) < span.to)
  const onNow = (p: Programme) => Date.parse(p.start) <= now && Date.parse(p.stop) > now
  const leftEdge = () => span.from + ((scroller.current?.scrollLeft ?? 0) / (REM_PER_MIN * remPx())) * 60_000
  const anchor = useRef<number | null>(null) // the time Up and Down keep to, however long the programmes they cross
  const keepAnchor = useRef(false)
  const pointer = useRef(false)
  // An Up or Down whose target is still loading: it is made when the target arrives, if focus has not moved since.
  const pending = useRef<{ key: string; row: number; id: string | null; dir: string } | null>(null)
  useEffect(() => {
    const byKey = () => { pointer.current = false }
    window.addEventListener('keydown', byKey, true)
    return () => window.removeEventListener('keydown', byKey, true)
  }, [])
  const across = useRef(false) // the focus now arriving was moved by Left or Right
  /**
   * Bring a cell focused by the keyboard out from under the sticky header and channel column. Sideways
   * the view moves only for Left and Right, and then by whole programmes: back to the start of the one
   * before, on so that the next one starts at the channel column. Up and Down land on what is on at the
   * hour in view, which needs no move (a long programme's start may be hours to the left).
   */
  const reveal = (node: HTMLElement, programme: boolean) => {
    const el = scroller.current
    if (!el) return
    const px = remPx(), box = el.getBoundingClientRect(), r = node.getBoundingClientRect()
    const top = box.top + HEAD_REM * px, bottom = box.top + el.clientHeight
    if (r.top < top) el.scrollTop -= top - r.top
    else if (r.bottom > bottom) el.scrollTop += r.bottom - bottom
    if (!programme) return
    const left = box.left + parseFloat(getComputedStyle(el).getPropertyValue('--epg-ch')) * px, right = box.left + el.clientWidth
    const hidden = r.right <= left + 1 || r.left >= right - 1
    // Less than a column of it showing at the right edge is not enough to read.
    if (hidden || (across.current && (r.left < left || r.left + Math.min(r.width, COL_REM * px) > right))) el.scrollLeft += r.left - left
  }
  const above = wide ? upKey : 'guide:details'
  const actions = {
    focusCell: (c: Channel, p: Programme | null, node: HTMLElement) => {
      if (!pointer.current) reveal(node, !!p)
      across.current = false
      // What is on now is watched from now, a later programme from its start; neither from before the edge of the view.
      if (p && !keepAnchor.current) anchor.current = Math.max(onNow(p) ? Date.now() : Date.parse(p.start), leftEdge())
      keepAnchor.current = false
      setSel(p)
      onSelectChannel(c.id)
    },
    move: (row: number, id: string | null, dir: string): boolean => {
      const c = channels[row]
      if (!c) return true
      if (dir === 'left' || dir === 'right') {
        const list = drawnOf(c)
        const next = id === null ? (dir === 'right' ? programmeAt(list, leftEdge()) : null) : list[list.findIndex((p) => p.id === id) + (dir === 'right' ? 1 : -1)]
        if (next) { across.current = id !== null; void setFocus(cellKey(c.id, next.id)) }
        else if (id !== null && dir === 'left') void setFocus(channelKey(c.id))
        // The last programme loaded: ask for later hours; focus stays put for the next press.
        else if (id !== null && span.to < horizon) setSpan({ from: span.from, to: Math.min(horizon, span.to + EXTEND_H * HOUR_MS) })
        return false
      }
      const to = row + (dir === 'down' ? 1 : -1), n = channels[to]
      const wait = () => { pending.current = { key: id === null ? channelKey(c.id) : cellKey(c.id, id), row, id, dir } }
      if (to < 0) void setFocus(id === null ? 'guide:now' : above)
      else if (!n) {
        // Past the last row loaded: focus stays while the next page comes, then moves on to it.
        if (hasNextPage) { wait(); if (!isFetchingNextPage) void fetchNextPage() }
      } else if (id !== null && !epg.byChannel[n.id] && loadingIds.has(n.id)) wait() // rather than drop to the channel column
      else {
        const p = id === null ? null : programmeAt(drawnOf(n), anchor.current ?? leftEdge())
        const target = p ? cellKey(n.id, p.id) : channelKey(n.id)
        // The row above and below the focused one are always drawn (rowsToDraw); a press that beats their render waits for the next.
        if (doesFocusableExist(target)) { keepAnchor.current = true; void setFocus(target) }
      }
      return false
    },
    open: (c: Channel, p: Programme) => {
      const start = Date.parse(p.start), stop = Date.parse(p.stop)
      if (stop <= Date.now()) {
        if (p.catchup_available) setCatchup({ ch: c, p })
        else toast('Catch-up is not available for this programme.')
      } else if (recordable) setRecording({ ch: c, id: p.id })
      else if (start <= Date.now()) go(`/play/${encodeURIComponent(c.id)}`)
      else toast(`${p.title} starts at ${fmtTime(p.start)}.`)
    },
    play: (c: Channel) => go(`/play/${encodeURIComponent(c.id)}`),
  }
  // Rows are memoised: they get handlers that never change and that call what this render defined.
  const live = useRef(actions)
  useEffect(() => { live.current = actions })
  const onFocusCell = useCallback((c: Channel, p: Programme | null, node: HTMLElement) => live.current.focusCell(c, p, node), [])
  const onMove = useCallback((row: number, id: string | null, dir: string) => live.current.move(row, id, dir), [])
  const onOpen = useCallback((c: Channel, p: Programme) => live.current.open(c, p), [])
  const onPlay = useCallback((c: Channel) => live.current.play(c), [])
  useEffect(() => {
    const want = pending.current
    pending.current = null
    if (want && getCurrentFocusKey() === want.key) live.current.move(want.row, want.id, want.dir)
  }, [channels, epg.byChannel])

  // The guide's one tab stop, and where focus arrives: the chosen programme, else what is on at the hour in view.
  const homeRow = focusRow ?? Math.min(view?.row ?? 0, count - 1)
  const home = useMemo(() => {
    const c = channels[homeRow]
    if (!c) return upKey
    const list = epg.byChannel[c.id] ?? []
    const at = view && (now < view.from || now >= view.to) ? view.from : now
    const p = (sel?.channel_id === c.id && list.find((x) => x.id === sel.id)) || list.find((x) => Date.parse(x.start) <= at && Date.parse(x.stop) > at)
    return p ? cellKey(c.id, p.id) : channelKey(c.id)
  }, [channels, homeRow, epg.byChannel, sel, view, now, upKey])
  useRestoreFocus(ready && !epg.loading.includes(true), home)

  const goNow = () => {
    const t = Date.now()
    // A guide left open for hours has a window that no longer holds the present: open a new one.
    if (t - SLOT_MS < span.from || t + 2 * HOUR_MS > span.to) setSpan(openingSpan(t, BACK_H, FWD_H))
    anchor.current = t
    setJump(startOfSlot(t - SLOT_MS))
  }

  /* ---------- the programme described beside (or under) the header ---------- */
  const focused = focusRow !== null ? channels[focusRow] ?? null : null
  // The focused programme as the guide has it now: its recording state changes while it stays selected.
  const chosen = sel && focused?.id === sel.channel_id ? epg.byChannel[sel.channel_id]?.find((x) => x.id === sel.id) ?? sel : null
  const described = chosen ?? (focused ? epg.byChannel[focused.id]?.find(onNow) ?? focused.now ?? null : null)
  const state = described?.recording_state ?? null
  const details: GuideDetailsProps = {
    channel: focused, programme: described, now, recordable,
    recording: state && described ? `${recordingStateLabel(state)}${state === 'failed' || state === 'missed' ? `: ${recordingOf(recordings.data?.recordings, described)?.error ?? 'no reason was given.'}` : ''}` : null,
    hint: `Move over a programme for details. Press OK on a past programme to watch it again where catch-up is offered${recordable ? ', or on a later one to record it' : ''}.`,
    onWatch: actions.play, onRecord: (c, p) => setRecording({ ch: c, id: p.id }),
    onCatchup: (c, p) => go(`/play/${encodeURIComponent(c.id)}?catchup=${encodeURIComponent(p.id)}`),
  }

  const failed = ch.error && !pages ? ch.error : epg.error && !Object.keys(epg.byChannel).length ? epg.error : null
  const minutes = (span.to - span.from) / 60_000
  const ticks = Array.from({ length: minutes / 30 }, (_, i) => span.from + i * SLOT_MS)
  const midnight = (t: number) => { const d = new Date(t); return d.getHours() === 0 && d.getMinutes() === 0 }
  return (
    <div data-testid="guide" className="epg-view" aria-label="Programme guide">
      {wide ? detailsSlot && createPortal(<GuideDetailsPanel {...details} />, detailsSlot) : <GuideDetailsLine {...details} />}
      {failed ? <ErrorState error={failed} onRetry={() => void ch.refetch()} /> : ch.isLoading ? <Skeleton className="epg-skel" /> : count === 0 ? (
        <Empty title="No channels match" hint={scheduledOnly ? 'No current schedules match these filters. Try All channels, another language, or a different search.' : 'Try another language or a different channel name.'}>
          {scheduledOnly && <Button focusKey="guide:show-all" onEnter={onShowAll}>Show channels without schedules</Button>}
        </Empty>
      ) : (
        <div ref={gridRef} className="epg" data-testid="guide-grid" onScroll={onScroll} onPointerMoveCapture={() => { pointer.current = true }}>
          <FocusGroup focusKey="guide:grid" className="epg-canvas" remember preferred={home} style={{ width: `calc(${minutes * REM_PER_MIN}rem + var(--epg-ch))`, height: `${HEAD_REM + count * ROW_REM + 2}rem` }}>
            <div className="epg-head">
              <div className="epg-corner">
                <Focusable focusKey="guide:now" className="epg-now fx-flat" scroll={false} label="Now" title="Back to the current time" onEnter={goNow}
                  onArrow={(d) => { const c = channels[homeRow]; if (d === 'up') void setFocus(above); else if (d === 'down' && c) void setFocus(channelKey(c.id)); else if (d === 'right' && c) void setFocus(home); return false }}>
                  <i aria-hidden="true" />Now
                </Focusable>
              </div>
              {ticks.map((t) => <div key={t} className="label epg-tick" style={{ width: `${COL_REM}rem` }}>{midnight(t) ? `${new Date(t).toLocaleDateString([], { weekday: 'short' })} ` : ''}{fmtTime(t)}</div>)}
            </div>
            <div className="epg-rows">
              {drawn.map((i) => {
                const c = channels[i]!
                const tabKey = i === homeRow ? home : null
                return <GuideRow key={c.id} index={i} channel={c} programmes={epg.byChannel[c.id] ?? NONE} loading={loadingIds.has(c.id)} span={span} now={now} tabKey={tabKey}
                  onFocusCell={onFocusCell} onMove={onMove} onOpen={onOpen} onPlay={onPlay} />
              })}
            </div>
            <div className="epg-foot label" style={{ top: `${HEAD_REM + count * ROW_REM}rem` }} role="status" data-testid="guide-end">
              <span>{isFetchNextPageError ? <>More channels could not be loaded. <Button focusKey="guide:more" className="btn-chip" onEnter={() => void fetchNextPage()}>Try again</Button></>
                : hasNextPage ? 'Loading more channels…' : `End of the list · ${total.toLocaleString()} channels`}</span>
            </div>
            <div aria-hidden="true" data-testid="now-line" className="epg-nowline" style={{ left: `calc(var(--epg-ch) + ${((now - span.from) / 60_000) * REM_PER_MIN}rem)` }} />
          </FocusGroup>
        </div>
      )}
      <RecordDialog target={recording && recordingProgramme ? { channel: recording.ch, programmes: [recordingProgramme] } : null} onClose={() => setRecording(null)}
        onWatch={() => recording && go(`/play/${encodeURIComponent(recording.ch.id)}`)} />
      <Modal open={!!catchup} onClose={() => setCatchup(null)} title="Watch from the start" firstFocusKey="cu:play">
        {catchup && (
          <>
            <p>{catchup.p.title} on {catchup.ch.name}, aired at {fmtTime(catchup.p.start)}.</p>
            <div className="flex gap-3">
              <Button focusKey="cu:play" primary icon="play" onEnter={() => { const c = catchup; setCatchup(null); go(`/play/${encodeURIComponent(c.ch.id)}?catchup=${encodeURIComponent(c.p.id)}`) }}>Play catch-up</Button>
              <Button focusKey="cu:cancel" onEnter={() => setCatchup(null)}>Cancel</Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}
const NONE: readonly Programme[] = []
