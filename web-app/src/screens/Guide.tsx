import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useEpgChunks, useRecordings, type ChannelFilters, type EpgRequest } from '../api/hooks'
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
import { SLOT_MS, extendedEnd, openingSpan, programmeAt, spanChunks, type TimeSpan } from '../lib/guideWindow'
import { RecordDialog } from '../recordings/RecordDialog'
import { GuideDetailsLine, GuideDetailsPanel, type GuideDetailsProps } from './GuideDetails'
import { COL_REM, GuideRow, HEAD_REM, REM_PER_MIN, ROW_REM, cellKey, channelKey } from './GuideRow'
import { channelsEnd, useChannelFeed, useChannelRows } from './useChannelFeed'
import { startOfSlot, useGuideView, type GuideView } from './useGuideView'
import { remPx } from './useRowView'

const HOUR_MS = 3600_000
const BACK_H = 3
const FWD_H = 9
// Later hours are asked for as the viewer nears the end of what is loaded, up to a day ahead.
const EXTEND_H = 4
const HORIZON_H = 24

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
  const [view, setView] = useHistoryState<GuideView | null>('guide:view', null)
  const key = JSON.stringify([filters, scheduledOnly])
  const [filterKey, setFilterKey] = useHistoryState('guide:filters', key)
  // Other filters start at the first channel; the hour on screen stays.
  if (filterKey !== key) { setFilterKey(key); setSel(null); setView((v) => v && { ...v, row: 0 }) }

  const feed = useChannelFeed({ ...filters, has_epg: scheduledOnly || undefined })
  const { query: ch, pages, channels, count, total, rowOf } = feed
  const focusRow = (selectedChannelId !== null ? rowOf.get(selectedChannelId) : undefined) ?? null
  const { scroller, gridRef, onScroll, win, placed, jumpTo, pointer, onPointerMove, revealRow } = useGuideView({ filterKey: key, count, span, view, setView, morePages: !!ch.hasNextPage })
  // Channels load as the view or the focus nears the last one loaded.
  const drawn = useChannelRows(feed, { win, placed }, view?.row ?? null, focusRow)

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
  // Recording: where the server has it on and this profile may schedule, OK on a programme that is on or still to come opens Record.
  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const [recording, setRecording] = useState<{ ch: Channel; id: string } | null>(null)
  const recordingProgramme = recording && epg.byChannel[recording.ch.id]?.find((x) => x.id === recording.id)

  useEffect(() => { if (pages) setChannelList(pages[0]?.list_id ?? null, channels) }, [pages, channels, setChannelList])
  useEffect(() => { onTotal(pages ? total : null) }, [pages, total, onTotal])

  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = ch
  const ready = placed && count > 0

  /* ---------- later hours as you go ---------- */
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
  // An Up or Down whose target is still loading: it is made when the target arrives, if focus has not moved since.
  const pending = useRef<{ key: string; row: number; id: string | null; dir: string } | null>(null)
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
    revealRow(node)
    if (!programme) return
    const px = remPx(), box = el.getBoundingClientRect(), r = node.getBoundingClientRect()
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
    jumpTo(startOfSlot(t - SLOT_MS))
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
  // Hours of another day say which: scrolled to tomorrow morning, "6:00 AM" alone would read as today's.
  const today = new Date(now).toDateString()
  const dayOf = (t: number) => { const d = new Date(t); return d.getMinutes() === 0 && d.toDateString() !== today ? `${d.toLocaleDateString([], { weekday: 'short' })} ` : '' }
  return (
    <div data-testid="guide" className="epg-view" aria-label="Programme guide">
      {wide ? detailsSlot && createPortal(<GuideDetailsPanel {...details} />, detailsSlot) : <GuideDetailsLine {...details} />}
      {failed ? <ErrorState error={failed} onRetry={() => void ch.refetch()} /> : ch.isLoading ? <Skeleton className="epg-skel" /> : count === 0 ? (
        <Empty title="No channels match" hint={scheduledOnly ? 'No current schedules match these filters. Try All channels, another language, or a different search.' : 'Try another language or a different channel name.'}>
          {scheduledOnly && <Button focusKey="guide:show-all" onEnter={onShowAll}>Show channels without schedules</Button>}
        </Empty>
      ) : (
        <div ref={gridRef} className="epg" data-testid="guide-grid" onScroll={onScroll} onPointerMoveCapture={onPointerMove}>
          <FocusGroup focusKey="guide:grid" className="epg-canvas" remember preferred={home} style={{ width: `calc(${minutes * REM_PER_MIN}rem + var(--epg-ch))`, height: `${HEAD_REM + count * ROW_REM + 2}rem` }}>
            <div className="epg-head">
              <div className="epg-corner">
                <Focusable focusKey="guide:now" className="epg-now fx-flat" scroll={false} label="Now" title="Back to the current time" onEnter={goNow}
                  onArrow={(d) => { const c = channels[homeRow]; if (d === 'up') void setFocus(above); else if (d === 'down' && c) void setFocus(channelKey(c.id)); else if (d === 'right' && c) void setFocus(home); return false }}>
                  <i aria-hidden="true" />Now
                </Focusable>
              </div>
              {ticks.map((t) => <div key={t} className="label epg-tick" style={{ width: `${COL_REM}rem` }}>{dayOf(t)}{fmtTime(t)}</div>)}
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
                : channelsEnd(hasNextPage, total)}</span>
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
