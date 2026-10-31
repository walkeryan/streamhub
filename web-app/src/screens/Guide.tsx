import { useEffect, useMemo, useRef, useState } from 'react'
import { useChannels, useEpg, type ChannelFilters } from '../api/hooks'
import type { Channel, Programme } from '../api/types'
import { Button, Chip, Empty, ErrorState, LogoBlock, Modal, Skeleton } from '../components/ui'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useUi } from '../store/ui'
import { useHistoryState } from '../nav/historyState'
import { fmtTime, useNow } from '../lib/util'

const COL_REM = 13 // 30-minute column width, see .epg --col
const REM_PER_MIN = COL_REM / 30
const BACK_H = 3
const FWD_H = 9
const MAX_CH = 40

export function Guide({ filters, selectedChannelId, onSelectChannel }: {
  filters: ChannelFilters; selectedChannelId: string | null; onSelectChannel: (id: string) => void
}) {
  const now = useNow(30_000)
  const [page, setPage] = useHistoryState('guide:page', 0)
  const [scheduledOnly, setScheduledOnly] = useHistoryState('guide:scheduled', true)
  const [sel, setSel] = useHistoryState<Programme | null>('guide:selected-programme', null)
  const key = JSON.stringify(filters)
  const [filterKey, setFilterKey] = useHistoryState('guide:filters', key)
  if (filterKey !== key) { setFilterKey(key); setPage(0); setSel(null) }
  const ch = useChannels({ ...filters, has_epg: scheduledOnly || undefined, limit: MAX_CH, offset: page * MAX_CH })
  const channels = useMemo(() => ch.data?.channels ?? [], [ch.data])
  const total = ch.data?.total ?? channels.length
  const pages = Math.max(1, Math.ceil(total / MAX_CH))
  const setChannelList = useUi((s) => s.setChannelList)
  const toast = useUi((s) => s.toast)
  const go = useGo()
  const slot = 30 * 60_000
  const [t0, setT0] = useHistoryState('guide:time', Math.floor((Date.now() - BACK_H * 3600_000) / slot) * slot)
  const t1 = t0 + (BACK_H + FWD_H) * 3600_000
  const epg = useEpg(channels.map((c) => c.id), new Date(t0).toISOString(), new Date(t1).toISOString())
  const [catchup, setCatchup] = useState<{ ch: Channel; p: Programme } | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => { if (ch.data) setChannelList(ch.data.list_id ?? null, ch.data.channels ?? []) }, [ch.data, setChannelList])
  useEffect(() => {
    if (!epg.data || !scroller.current) return
    scroller.current.scrollLeft = Math.max(0, ((Date.now() - 30 * 60_000 - t0) / 60_000) * REM_PER_MIN * parseFloat(getComputedStyle(document.documentElement).fontSize))
  }, [epg.data, t0])

  const firstNow = useMemo(() => {
    const c = channels.find((c) => c.id === selectedChannelId) ?? channels[0]
    const p = c && epg.data?.[c.id]?.find((x) => Date.parse(x.start) <= Date.now() && Date.parse(x.stop) > Date.now())
    return c && p ? `epg::${c.id}::${p.id}` : c ? `guide:ch:${c.id}` : 'live:search'
  }, [channels, epg.data, selectedChannelId])
  useRestoreFocus(!!epg.data && channels.length > 0, firstNow)

  const totalMin = (BACK_H + FWD_H) * 60
  const nowLeft = ((now - t0) / 60_000) * REM_PER_MIN
  const ticks = Array.from({ length: totalMin / 30 }, (_, i) => t0 + i * slot)

  const open = (c: Channel, p: Programme) => {
    const start = Date.parse(p.start), stop = Date.parse(p.stop)
    if (stop <= Date.now()) {
      if (p.catchup_available) setCatchup({ ch: c, p })
      else toast('Catch-up is not available for this programme.')
    } else if (start <= Date.now()) go(`/play/${encodeURIComponent(c.id)}`)
    else toast(`${p.title} starts at ${fmtTime(p.start)}.`)
  }

  if (ch.error || epg.error) return <div className="page-pad"><ErrorState error={ch.error ?? epg.error} onRetry={() => { void ch.refetch(); void epg.refetch() }} /></div>
  return (
    <div data-testid="guide" aria-label="Programme guide">
      <FocusGroup focusKey="guide:filters" className="flex flex-col gap-3 mb-3">
        <div className="flex gap-2 flex-wrap items-center">
          <Chip focusKey="guide:scheduled" pressed={scheduledOnly} onEnter={() => { setScheduledOnly(true); setPage(0) }}>With schedules</Chip>
          <Chip focusKey="guide:all-channels" pressed={!scheduledOnly} onEnter={() => { setScheduledOnly(false); setPage(0) }}>All channels</Chip>
          <Button focusKey="guide:now" onEnter={() => {
            const start = Math.floor((Date.now() - BACK_H * 3600_000) / slot) * slot
            setT0(start)
            if (scroller.current) scroller.current.scrollLeft = Math.max(0, ((Date.now() - 30 * 60_000 - start) / 60_000) * REM_PER_MIN * parseFloat(getComputedStyle(document.documentElement).fontSize))
          }}>Now</Button>
        </div>
        <div className="flex gap-3 flex-wrap items-center" aria-label="Guide pages">
          <Button focusKey="guide:previous" disabled={page === 0 || ch.isFetching} onEnter={() => { setPage((p) => Math.max(0, p - 1)); setSel(null) }}>Previous channels</Button>
          <span className="label" role="status">{ch.isFetching ? 'Loading channels…' : `${total.toLocaleString()} channels · Page ${page + 1} of ${pages}`}</span>
          <Button focusKey="guide:next" disabled={page + 1 >= pages || ch.isFetching} onEnter={() => { setPage((p) => p + 1); setSel(null) }}>Next channels</Button>
        </div>
      </FocusGroup>
      {ch.isLoading || ch.isPlaceholderData || epg.isLoading ? <Skeleton style={{ height: '24rem' }} /> : channels.length === 0 ? (
        <Empty title="No channels match" hint={scheduledOnly ? 'No current schedules match these filters. Try All channels, another language, or a different search.' : 'Try another language or a different channel name.'}>
          {scheduledOnly && <Button focusKey="guide:show-all" onEnter={() => { setScheduledOnly(false); setPage(0) }}>Show channels without schedules</Button>}
        </Empty>
      ) : (
        <>
          <div ref={scroller} className="epg" style={{ overflow: 'auto', maxHeight: 'calc(100dvh - 24rem)', minHeight: '14rem', borderRadius: 'var(--radius-md)', background: 'var(--t-surface)' }}>
            <div style={{ width: `calc(${totalMin * REM_PER_MIN}rem + 13rem)`, position: 'relative' }}>
              <div style={{ display: 'flex', position: 'sticky', top: 0, zIndex: 20, background: 'var(--t-surface)', height: '2rem' }}>
                <div style={{ width: '13rem', flex: 'none', position: 'sticky', left: 0, zIndex: 21, background: 'var(--t-surface)' }} />
                {ticks.map((t) => <div key={t} className="label" style={{ width: `${COL_REM}rem`, flex: 'none', paddingLeft: '0.5rem', borderLeft: '1px solid color-mix(in srgb, var(--t-text) 12%, transparent)', lineHeight: '2rem' }}>{fmtTime(t)}</div>)}
              </div>
              <FocusGroup focusKey="guide:grid" remember>
                {channels.map((c) => (
                  <FocusGroup key={c.id} focusKey={`guide:row:${c.id}`} className="" remember>
                    <div style={{ display: 'flex', height: '3.5rem' }}>
                      <div style={{ width: '13rem', flex: 'none', position: 'sticky', left: 0, zIndex: 10, background: 'var(--t-surface)', padding: '0.25rem' }}>
                        <Focusable focusKey={`guide:ch:${c.id}`} className="flex items-center gap-2 h-full px-2 rounded-md fx-flat" label={`${c.number} ${c.name}`} onFocus={() => onSelectChannel(c.id)} onEnter={() => go(`/play/${encodeURIComponent(c.id)}`)}>
                          <span className="label" style={{ width: '1.75rem', textAlign: 'right' }}>{c.number}</span>
                          <LogoBlock name={c.name} logo={c.logo} />
                          <span className="truncate" style={{ fontSize: 'var(--text-label)', fontWeight: 600 }}>{c.name}</span>
                        </Focusable>
                      </div>
                      <div style={{ position: 'relative', flex: 1, borderTop: '1px solid color-mix(in srgb, var(--t-text) 8%, transparent)' }}>
                        {(epg.data?.[c.id] ?? []).length === 0 && (
                          <span className="label muted" style={{ position: 'sticky', left: '13.5rem', display: 'inline-block', padding: '1rem 0.5rem' }}>
                            {c.epg_id ? 'No schedule supplied for this time' : 'No guide mapping supplied'}
                          </span>
                        )}
                        {(epg.data?.[c.id] ?? []).map((p) => {
                          const s = Math.max(Date.parse(p.start), t0), e = Math.min(Date.parse(p.stop), t1)
                          if (e <= s) return null
                          const isNow = Date.parse(p.start) <= now && Date.parse(p.stop) > now
                          const past = Date.parse(p.stop) <= now
                          return (
                            <Focusable key={p.id} focusKey={`epg::${c.id}::${p.id}`} className="epg-cell fx-flat" label={`${c.name}: ${p.title}, ${fmtTime(p.start)}`}
                              data={{ now: isNow, past }} onEnter={() => open(c, p)} onFocus={() => { setSel(p); onSelectChannel(c.id) }}
                              style={{ left: `${((s - t0) / 60_000) * REM_PER_MIN}rem`, width: `calc(${((e - s) / 60_000) * REM_PER_MIN}rem - 0.25rem)`, position: 'absolute' }}>
                              <span className="epg-label">
                                <span className="truncate" style={{ fontWeight: 600 }}>{p.title}</span>
                                <span className="label truncate">{fmtTime(p.start)}{p.catchup_available && past ? ' · Catch-up' : ''}{isNow ? ' · On now' : ''}</span>
                              </span>
                            </Focusable>
                          )
                        })}
                      </div>
                    </div>
                  </FocusGroup>
                ))}
              </FocusGroup>
              <div aria-hidden="true" data-testid="now-line" style={{ position: 'absolute', top: 0, bottom: 0, left: `calc(13rem + ${nowLeft}rem)`, width: 2, background: 'var(--t-danger)', zIndex: 15, pointerEvents: 'none' }} />
            </div>
          </div>
          <div className="mt-3" style={{ minHeight: '4.5rem' }} aria-live="polite" data-testid="guide-info">
            {sel ? (
              <>
                <h2 className="heading">{sel.title}</h2>
                <p className="muted">{fmtTime(sel.start)} to {fmtTime(sel.stop)}{sel.category ? ` · ${sel.category}` : ''}</p>
                {sel.description && <p className="label clamp2">{sel.description}</p>}
              </>
            ) : <p className="muted">Move over a programme for details. Press OK on a past programme to watch it again where catch-up is offered.</p>}
          </div>
        </>
      )}
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
