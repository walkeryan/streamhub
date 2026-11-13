import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useRecordings, useToggleFavorite, type ChannelFilters } from '../api/hooks'
import type { Channel } from '../api/types'
import { Button, Chip, ErrorState, Skeleton } from '../components/ui'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { useHistoryState } from '../nav/historyState'
import { useGo, useRestoreFocus } from '../nav/back'
import { Preview } from '../player/Preview'
import { useUi } from '../store/ui'
import { fmtTime } from '../lib/util'
import { useCan } from '../api/me'
import { RecordDialog } from '../recordings/RecordDialog'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { channelsEnd, useChannelFeed, useChannelRows } from './useChannelFeed'
import { useRowView } from './useRowView'

const ROW_REM = 6.5 // a row and the gap under it, see .chan-list --chan-row
const CLEAR_REM = 0.375 // what a focused row's ring needs inside the list's edges, see .chan-list
const PREVIEW_KEY = 'sh.live.preview'
const rowKey = (channelId: string) => `live::${channelId}`

/**
 * Live TV's Channels view: the list takes what the window has left under the header and scrolls inside
 * itself, loading channels as its end comes near and drawing only the rows near the view, as the guide does
 * (useChannelFeed, useRowView). The preview beside it stays put. A phone has the list alone.
 */
export function Channels({ filters, selectedChannelId, onSelectChannel, upKey, emptyKey, empty, onTotal, phone }: {
  filters: ChannelFilters
  selectedChannelId: string | null; onSelectChannel: (id: string) => void
  /** Where Up from the first row goes: the header's row of groups. */
  upKey: string
  /** Where focus arrives when no channel matches, and what that list offers instead. */
  emptyKey: string; empty: ReactNode
  onTotal: (total: number | null) => void
  phone: boolean
}) {
  const [row, setRow] = useHistoryState<number | null>('live:row', null)
  const key = JSON.stringify(filters)
  const [filterKey, setFilterKey] = useHistoryState('live:filters', key)
  // Other filters start at the first channel.
  if (filterKey !== key) { setFilterKey(key); setRow(0) }

  const feed = useChannelFeed(filters)
  const { query, pages, channels, count, total, listId, rowOf } = feed
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query
  const focusRow = (selectedChannelId !== null ? rowOf.get(selectedChannelId) : undefined) ?? null
  const onMeasure = useCallback((_: HTMLDivElement, top: number) => setRow(top), [setRow])
  const { boxRef, onScroll, win, placed, pointer, onPointerMove, revealRow } = useRowView({ filterKey: key, count, rowRem: ROW_REM, row, morePages: !!hasNextPage, onMeasure })
  const drawn = useChannelRows(feed, { win, placed }, row, focusRow)
  const focused = focusRow !== null ? channels[focusRow] ?? null : null

  const setChannelList = useUi((s) => s.setChannelList)
  const go = useGo()
  useEffect(() => { if (pages) setChannelList(listId, channels) }, [pages, listId, channels, setChannelList])
  useEffect(() => { onTotal(pages ? total : null) }, [pages, total, onTotal])
  const play = (c: Channel) => {
    setChannelList(listId, channels)
    go(`/play/${encodeURIComponent(c.id)}${listId ? `?list=${encodeURIComponent(listId)}` : ''}`)
  }

  /* ---------- focus: Up and Down by row, wherever the wheel has taken the view ---------- */
  // A Down whose target is still loading: it is made when the next page arrives, if focus has not moved since.
  const pending = useRef<{ key: string; row: number } | null>(null)
  const move = (index: number, dir: string): boolean => {
    // Sideways is the preview's controls, found the usual way.
    if (dir !== 'up' && dir !== 'down') return true
    const to = index + (dir === 'down' ? 1 : -1), next = channels[to]
    if (to < 0) void setFocus(upKey)
    else if (!next) {
      // Past the last row loaded: focus stays while the next page comes, then moves on to it.
      if (hasNextPage) { pending.current = { key: rowKey(channels[index]!.id), row: index }; if (!isFetchingNextPage) void fetchNextPage() }
    }
    // The row above and below the focused one are always drawn (rowsToDraw); a press that beats their render waits for the next.
    else if (doesFocusableExist(rowKey(next.id))) void setFocus(rowKey(next.id))
    return false
  }
  const live = useRef(move)
  useEffect(() => { live.current = move })
  useEffect(() => {
    const want = pending.current
    pending.current = null
    if (want && getCurrentFocusKey() === want.key) live.current(want.row, 'down')
  }, [channels])
  // The list's one tab stop, and where focus arrives: the chosen channel, else the first one in view.
  const homeRow = focusRow ?? Math.min(row ?? 0, count - 1)
  const home = channels[homeRow] ? rowKey(channels[homeRow].id) : emptyKey
  const ready = !!pages && (count === 0 || placed)
  // Arrive on the channels, not on the search field (a focused field raises the on-screen keyboard).
  useRestoreFocus(ready, home)

  /* ---------- the channel in focus, beside the list ---------- */
  // Off until the viewer asks: every preview holds one of the source's few connections. The choice
  // is kept for this browser, so someone who wants previews turns them on once.
  const [previewEnabled, setPreviewOn] = useState(() => { try { return localStorage.getItem(PREVIEW_KEY) === '1' } catch { return false } })
  const setPreviewEnabled = (next: boolean | ((was: boolean) => boolean)) => setPreviewOn((was) => {
    const on = typeof next === 'function' ? next(was) : next
    try { localStorage.setItem(PREVIEW_KEY, on ? '1' : '0') } catch { /* private window: the choice lasts for the visit */ }
    return on
  })
  const [previewId, setPreviewId] = useState<string | null>(null)
  useEffect(() => {
    if (!previewEnabled || !focused) { setPreviewId(null); return }
    const id = window.setTimeout(() => setPreviewId(focused.id), 600)
    return () => window.clearTimeout(id)
  }, [focused, previewEnabled])
  const toggleFav = useToggleFavorite()
  // Recording the focused channel's now or next, where the server has recording on and this profile may schedule.
  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const [recordingId, setRecordingId] = useState<string | null>(null)
  const recordingChannel = channels.find((c) => c.id === recordingId)

  return <div className="live-catalog-layout" data-testid="live-channels" data-preview={!phone}>
    {query.error && !pages ? <div><ErrorState error={query.error} onRetry={() => void query.refetch()} /></div>
      : !pages ? <div className="chan-list-skel">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} />)}</div>
      : count === 0 ? <div>{empty}</div> : (
        <div ref={boxRef} className="chan-list" data-testid="channel-list" onScroll={onScroll} onPointerMoveCapture={onPointerMove}>
          <FocusGroup focusKey="live:list" className="chan-list-canvas" role="listbox" label="Channels" remember preferred={home} style={{ height: `${count * ROW_REM + 2}rem` }}>
            {drawn.map((i) => {
              const c = channels[i]!
              return <ChannelRow key={c.id} focusKey={rowKey(c.id)} channel={c} style={{ top: `${i * ROW_REM}rem` }} scroll={false}
                data={{ row: String(i), 'tab-group': 'channels', 'tab-current': i === homeRow }}
                onEnter={() => play(c)} onArrow={(d) => move(i, d)} onFocus={(layout) => { if (!pointer.current) revealRow(layout.node, CLEAR_REM); onSelectChannel(c.id) }} />
            })}
            <div className="chan-list-foot label" style={{ top: `${count * ROW_REM}rem` }} role="status" data-testid="channels-end">
              {isFetchNextPageError ? <>More channels could not be loaded. <Button focusKey="live:more" className="btn-chip" onEnter={() => void fetchNextPage()}>Try again</Button></>
                : channelsEnd(hasNextPage, total)}
            </div>
          </FocusGroup>
        </div>
      )}
    {/* A phone has no room for a preview: selecting a channel plays it. */}
    {!phone && <aside className="live-catalog-preview" aria-label="Preview">
      <Preview channelId={previewId} off={!previewEnabled} onTurnOn={() => setPreviewEnabled(true)} />
      {focused && <div className="flex flex-col gap-1" data-testid="live-info"><h2 className="title-xl">{focused.number} · {focused.name}</h2><p className="flex items-center gap-2 flex-wrap">{focused.now ? `Now: ${focused.now.title} (${fmtTime(focused.now.start)} to ${fmtTime(focused.now.stop)})` : 'No programme data supplied for this channel'}<RecordingBadge state={focused.now?.recording_state} /></p>{focused.next && <p className="muted flex items-center gap-2 flex-wrap">Next: {focused.next.title} at {fmtTime(focused.next.start)}<RecordingBadge state={focused.next.recording_state} /></p>}</div>}
      <FocusGroup focusKey="live:actions" className="flex gap-2 flex-wrap">
        <Button focusKey="live:watch" primary icon="play" disabled={!focused} onEnter={() => focused && play(focused)}>Watch full screen</Button>
        <Chip focusKey="live:preview" pressed={previewEnabled} onEnter={() => setPreviewEnabled((v) => !v)}>Preview {previewEnabled ? 'on' : 'off'}</Chip>
        <Button focusKey="live:togglefav" icon="heart" disabled={!focused} pressed={!!focused?.is_favorite} onEnter={() => focused && toggleFav.mutate({ kind: 'channel', id: focused.id, on: !focused.is_favorite })}>{focused?.is_favorite ? 'Remove favorite' : 'Add favorite'}</Button>
        {recordable && <Button focusKey="live:record" icon="record" disabled={!focused?.now && !focused?.next} onEnter={() => focused && setRecordingId(focused.id)} testId="live-record">Record</Button>}
      </FocusGroup>
    </aside>}
    <RecordDialog target={recordingChannel ? { channel: recordingChannel, programmes: [recordingChannel.now, recordingChannel.next].filter((p) => !!p) } : null} onClose={() => setRecordingId(null)} />
  </div>
}
