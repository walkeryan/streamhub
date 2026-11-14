import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { api, unwrap } from '../api/client'
import { useCreateRecordingRule, useDeleteRecording, useRecordings, useScheduleRecording } from '../api/hooks'
import { useCan } from '../api/me'
import type { components } from '../api/schema'
import type { Recording } from '../api/types'
import { Button, Chip, ErrorState, LogoBlock, Modal, Skeleton } from '../components/ui'
import { LanguageFilter } from '../components/LanguageFilter'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { useGo, useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { recordingOf } from '../lib/recordings'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { LANG_NAMES, cn, fmtTime, useDebounced, useNow } from '../lib/util'
import { MatchupArt, MatchupCard, MatchupTitle, matchupSpeech, matchupTitle } from './SportsMatchup'

type Event = components['schemas']['SportsEvent']
type Feed = components['schemas']['SportsEventFeed']
type EventsPage = components['schemas']['SportsEventsPage']
type Network = components['schemas']['SportsChannel']
type NetworksPage = components['schemas']['SportsChannelsPage']

const PAGE = 100
// One sport's row: enough to scroll through a Saturday's slate; the rest is a "See all" away.
const ROW = 40
const NETWORKS = 24
// Days are the viewer's own: the server is told the zone, and every time is shown on the local clock.
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const two = (n: number) => String(n).padStart(2, '0')
const dayOf = (at: number | string | Date) => { const d = new Date(at); return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}` }
const dateOf = (day: string) => { const [y, m, d] = day.split('-').map(Number); return new Date(y!, m! - 1, d!) }
const moveDay = (day: string, delta: number) => { const d = dateOf(day); d.setDate(d.getDate() + delta); return dayOf(d) }
const dayLabel = (day: string, today: string) => day === today ? 'Today' : day === moveDay(today, 1) ? 'Tomorrow' : dateOf(day).toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' })
const isLive = (event: Event, now: number) => event.kind !== 'channel' && Date.parse(event.start) <= now && now < Date.parse(event.stop)
const isAhead = (event: Event, now: number) => event.kind !== 'channel' && Date.parse(event.start) > now
const cardKey = (event: Event) => `sports:event:${event.id}`
const channelLine = (feeds: Feed[]) => feeds.length === 1 ? `${feeds[0]!.channel_name}${feeds[0]!.channel_number ? ` · Ch ${feeds[0]!.channel_number}` : ''}` : `${feeds.length} channels`
/** The household's recording of an event, on whichever of its channels it was scheduled. */
const recordingFor = (recordings: Recording[] | undefined, event: Event) => event.feeds.map((feed) => recordingOf(recordings, { channel_id: feed.channel_id, start: event.start })).find(Boolean)
const filterSchema = z.object({ language: z.string().max(256), sport: z.string().max(256) })
// The classifier's names, as a row or chip reads them.
const SPORT_LABELS: Record<string, string> = { 'American football': 'Football', Football: 'Other football', 'Combat sports': 'Boxing & UFC', Motorsport: 'Racing', Sports: 'More sports' }
const sportLabel = (sport: string) => SPORT_LABELS[sport] ?? sport
const hueOf = (text: string) => [...text].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % 360
/** A row's order: games we know the sides of first, each day in time order. */
const rowOrder = (a: Event, b: Event) => dayOf(a.start).localeCompare(dayOf(b.start)) || Number(!a.matchup) - Number(!b.matchup) || Date.parse(a.start) - Date.parse(b.start)
const validateFilters = (raw: unknown) => { const parsed = filterSchema.safeParse(raw); return parsed.success ? parsed.data : null }

export function Sports() {
  const profileId = useSession((s) => s.profile?.id)
  return <SportsScreen key={profileId} />
}

/**
 * Live games first, then what is coming, then the always-on networks. One press plays: a live game
 * on one channel starts at once, one on several asks which, and a game still to come offers its
 * time and, where this profile may record, the recorder.
 */
function SportsScreen() {
  const profile = useSession((s) => s.profile)
  const go = useGo()
  const saved = useProfilePreference('sportsFilters.v1', { language: profile?.languages.length ? 'mine' : 'eng', sport: '' }, validateFilters)
  const { language, sport } = saved.value
  const setLanguage = (language: string) => saved.update((v) => ({ ...v, language }))
  const setSport = (sport: string) => saved.update((v) => ({ ...v, sport }))
  const now = useNow(15_000)
  const today = dayOf(now)
  const [search, setSearch] = useHistoryState('sports:search', '')
  const [picked, setDate] = useHistoryState('sports:date', today)
  // A day that has passed has nothing left to offer: the screen never lists what is over.
  const date = picked < today ? today : picked
  const [page, setPage] = useHistoryState('sports:page', 0)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [picker, setPicker] = useState<Event | null>(null)
  const [sheet, setSheet] = useState<Event | null>(null)
  const [interacted, setInteracted] = useState(false)
  useEffect(() => {
    const mark = () => setInteracted(true)
    window.addEventListener('keydown', mark, true)
    window.addEventListener('pointerdown', mark, true)
    window.addEventListener('pointermove', mark, true)
    return () => { window.removeEventListener('keydown', mark, true); window.removeEventListener('pointerdown', mark, true); window.removeEventListener('pointermove', mark, true) }
  }, [])
  const term = useDebounced(search, 300)
  const languages = language === 'mine' ? profile?.languages.join(',') || 'all' : language
  const filter = `${profile?.id}|${languages}|${sport}|${term}|${date}`
  const [previousFilter, setPreviousFilter] = useState(filter)
  if (previousFilter !== filter) { setPreviousFilter(filter); setPage(0); setPicker(null); setSheet(null) }
  const q = useQuery({
    queryKey: ['sports', profile?.id, 'events', filter, page],
    enabled: saved.ready && !!profile,
    // Today's page runs into tomorrow, so an evening visit still has something to come.
    queryFn: ({ signal }) => unwrap<EventsPage>(api.GET('/sports/events', { signal, params: { query: {
      date, timezone: ZONE, days: date === today ? 2 : 1, status: 'active', language: languages,
      sport: sport || undefined, q: term || undefined, limit: PAGE, offset: page * PAGE,
    } } })),
    staleTime: 15_000, refetchInterval: 30_000,
  })
  const networks = useQuery({
    queryKey: ['sports', profile?.id, 'channels', languages, term],
    enabled: saved.ready && !!profile,
    queryFn: ({ signal }) => unwrap<NetworksPage>(api.GET('/sports/channels', { signal, params: { query: { language: languages, q: term || undefined, limit: NETWORKS } } })),
    staleTime: 60_000, refetchInterval: 60_000,
  })
  // With no sport picked and nothing searched, the screen is rows of tiles: Live now, then one row per
  // sport, busiest first. Each row asks for its own sport's games so a busy sport cannot crowd out the rest.
  const rowsView = !sport && !term
  const rowSports = rowsView ? [...(q.data?.sports ?? [])].sort((a, b) => Number(a.name === 'Sports') - Number(b.name === 'Sports') || b.count - a.count).map((entry) => entry.name) : []
  const rows = useQueries({ queries: rowSports.map((name) => ({
    queryKey: ['sports', profile?.id, 'row', `${profile?.id}|${languages}|${date}`, name],
    enabled: saved.ready && !!profile,
    queryFn: ({ signal }: { signal: AbortSignal }) => unwrap<EventsPage>(api.GET('/sports/events', { signal, params: { query: {
      date, timezone: ZONE, days: date === today ? 2 : 1, status: 'upcoming', language: languages, sport: name, limit: ROW, offset: 0,
    } } })),
    staleTime: 30_000, refetchInterval: 60_000,
  })) })
  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const data = q.data
  const events = data?.events ?? []
  const live = date === today ? events.filter((event) => isLive(event, now)) : []
  // Another day's page still overlaps a late game of the evening before; that game belongs to its own day.
  const ahead = events.filter((event) => isAhead(event, now) && dayOf(event.start) >= date)
  const gameChannels = date === today ? events.filter((event) => event.kind === 'channel') : []
  const days = [...new Set(ahead.map((event) => dayOf(event.start)))].sort()
  const first = live[0] ?? ahead[0] ?? gameChannels[0]
  // Where focus goes when nothing else claims it: the first game, or the sport chips.
  const fallback = first ? cardKey(first) : 'sports:sport:all'
  useEffect(() => {
    if (!saved.ready || data || interacted) return
    const frame = requestAnimationFrame(() => setFocus('sports:sport:all'))
    return () => cancelAnimationFrame(frame)
  }, [saved.ready, data, interacted])
  useRestoreFocus(saved.ready && !!data && !interacted, fallback)
  if (data && page > Math.max(0, Math.ceil(data.total / PAGE) - 1)) setPage(Math.max(0, Math.ceil(data.total / PAGE) - 1))

  const watch = async (channelId: string, from?: Event) => {
    // Back from the player returns to the game that was chosen, not to a row of a closed picker.
    // Navigation remembers the focused key, and a focus change is queued: wait for it.
    if (from) { setPicker(null); await setFocus(cardKey(from)) }
    go(`/play/${encodeURIComponent(channelId)}`)
  }
  // A game that ends while its channels are being chosen is no longer offered.
  const pickerOver = !!picker && picker.kind !== 'channel' && !isLive(picker, now)
  useEffect(() => { if (pickerOver) setPicker(null) }, [pickerOver])
  // A game leaves the screen when it ends, or when a filter changes. If it held the focus, a remote
  // would have nowhere to move from.
  useEffect(() => {
    // Focus changes are queued, and a closing sheet hands focus back to its opener first: look afterwards.
    const id = window.setTimeout(() => {
      const key = getCurrentFocusKey()
      if (key?.startsWith('sports:') && !doesFocusableExist(key) && !document.querySelector('.scrim')) void setFocus(fallback)
    }, 80)
    return () => window.clearTimeout(id)
  })
  const open = (event: Event) => {
    if (isAhead(event, Date.now())) setSheet(event)
    else if (event.feeds.length === 1) void watch(event.feeds[0]!.channel_id)
    else if (event.feeds.length > 1) setPicker(event)
  }

  if (!saved.ready) return <div className="page-pad sports" data-testid="sports"><h1 className="display">Sports</h1>{saved.error ? <><p role="alert">{saved.error}</p><Button focusKey="sports:retry-prefs" onEnter={() => void saved.retry()}>Retry</Button></> : <p role="status">Loading saved filters…</p>}</div>
  const counts = data?.sports ?? []
  const total = counts.reduce((sum, entry) => sum + entry.count, 0)
  const languageLabel = language === 'mine' ? null : language === 'all' ? 'All languages' : LANG_NAMES[language] ?? language
  return <div className="page-pad sports" data-testid="sports">
    <div className="sports-head">
      <h1 className="display">Sports</h1>
      <div className="sports-tools">
        {(search || languageLabel) && <p className="label truncate" data-testid="sports-filter-summary">{[search && `“${search}”`, languageLabel].filter(Boolean).join(' · ')}</p>}
        <Button focusKey="sports:filters" ghost icon="search" onEnter={() => setFiltersOpen(true)} testId="sports-filters">Search and language</Button>
      </div>
    </div>
    <div className="sports-bar">
      <FocusGroup focusKey="sports:sports" className="sports-chips" role="group" label="Sport">
        <Chip focusKey="sports:sport:all" pressed={!sport} onEnter={() => setSport('')} testId="sports-chip">All{data ? ` · ${total}` : ''}</Chip>
        {counts.map((entry) => <Chip key={entry.name} focusKey={`sports:sport:${entry.name}`} pressed={sport === entry.name} onEnter={() => setSport(entry.name)} testId="sports-chip">{entry.name} · {entry.count}</Chip>)}
        {sport && data && !counts.some((entry) => entry.name === sport) && <Chip focusKey="sports:sport:saved" pressed onEnter={() => setSport('')} testId="sports-chip">{sport} · 0</Chip>}
      </FocusGroup>
      <FocusGroup focusKey="sports:days" className="sports-days" role="group" label="Day">
        <Chip focusKey="sports:day:today" pressed={date === today} onEnter={() => setDate(today)}>Today</Chip>
        <Chip focusKey="sports:day:tomorrow" pressed={date === moveDay(today, 1)} onEnter={() => setDate(moveDay(today, 1))}>Tomorrow</Chip>
        <Button focusKey="sports:day:prev" iconOnly icon="back" label="Previous day" disabled={date <= today} onEnter={() => setDate(moveDay(date, -1))} />
        <Button focusKey="sports:day:next" iconOnly icon="chevR" label="Next day" onEnter={() => setDate(moveDay(date, 1))} />
        {date > moveDay(today, 1) && <span className="label" data-testid="sports-date">{dayLabel(date, today)}</span>}
      </FocusGroup>
    </div>
    {saved.error && <p role="alert">{saved.error}</p>}
    {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : q.isPending ? <Skeleton style={{ height: '16rem' }} /> : data && <>
      {rowsView ? <>
        {date === today && <section className="sports-section" aria-label="Live now" data-testid="sports-live">
          <h2 className="heading">Live now</h2>
          {live.length ? <FocusGroup focusKey="sports:live" className="row-scroll sports-tiles" label="Live games">
            {live.map((event) => <GameTile key={event.id} event={event} now={now} today={today} recordings={recordings.data?.recordings} onOpen={open} />)}
          </FocusGroup> : <p className="sports-quiet" role="status">No games are live right now.{ahead[0] ? ` Next: ${ahead[0].title} ${dayOf(ahead[0].start) === today ? '' : `${dayLabel(dayOf(ahead[0].start), today).toLowerCase()} `}at ${fmtTime(ahead[0].start)}.` : ''}</p>}
        </section>}
        {rowSports.map((name, i) => {
          const row = rows[i]?.data
          const games = (row?.events ?? []).filter((event) => isAhead(event, now) && dayOf(event.start) >= date).sort(rowOrder)
          if (!games.length) return null
          const label = sportLabel(name)
          return <section key={name} className="sports-section" aria-label={label} data-testid="sports-row" data-sport={name}>
            <h2 className="heading">{label}</h2>
            <FocusGroup focusKey={`sports:row:${name}`} className="row-scroll sports-tiles" label={label}>
              {games.map((event) => <GameTile key={event.id} event={event} now={now} today={today} recordings={recordings.data?.recordings} onOpen={open} />)}
              {(row?.total ?? 0) > games.length && <Focusable focusKey={`sports:more:${name}`} className="sports-tile sports-more fx-flat" data-testid="sports-see-all" label={`See all ${label}`} onEnter={() => setSport(name)}>
                <span className="sports-more-text">See all</span>
                <span className="label">{row!.total} {label.toLowerCase()} listings</span>
              </Focusable>}
            </FocusGroup>
          </section>
        })}
        {!days.length && <section className="sports-section" aria-label="Coming up" data-testid="sports-upcoming">
          <p className="sports-quiet" role="status">{date === today ? 'Nothing more is listed for today or tomorrow.' : `No games are listed for ${dayLabel(date, today)}.`}</p>
        </section>}
      </> : <>
      {date === today && <section className="sports-section" aria-label="Live now" data-testid="sports-live">
        <h2 className="heading">Live now</h2>
        {live.length ? <FocusGroup focusKey="sports:live" className={cn('sports-grid', live.some((event) => event.matchup) && 'sports-grid-wide')} label="Live games">
          {live.map((event) => {
            const badge = <span className={event.live_evidence ? 'badge sports-live' : 'badge'}>{event.replay ? 'Replay' : event.live_evidence ? 'LIVE' : 'On now'}</span>
            const where = event.feeds.length === 1 ? `on ${event.feeds[0]!.channel_name}` : `${event.feeds.length} channels`
            const state = event.replay ? 'replay' : event.live_evidence ? 'live' : 'on now'
            // A football game with both sides known is a matchup card; the remote moves over it like any other card.
            return event.matchup
              ? <Focusable key={event.id} focusKey={cardKey(event)} className="sports-card sports-matchup fx-flat" data-testid="sports-listing" data={{ kind: 'live', matchup: true }}
                label={`Watch ${matchupSpeech(event, event.matchup)}, ${event.sport}, ${state}, ${where}`} onEnter={() => open(event)}>
                <MatchupCard event={event} matchup={event.matchup} badge={badge} channel={channelLine(event.feeds)} />
              </Focusable>
              : <Focusable key={event.id} focusKey={cardKey(event)} className="sports-card fx-flat" data-testid="sports-listing" data={{ kind: 'live' }}
                label={`Watch ${event.title}, ${event.sport}, ${event.live_evidence ? 'live' : 'on now'}, ${where}`} onEnter={() => open(event)}>
                <span className="sports-card-top">
                  {badge}
                  {event.feeds.some((feed) => feed.quality) && <span className="badge">{event.feeds.find((feed) => feed.quality)!.quality}</span>}
                  <span className="label truncate sports-card-sport">{event.sport}</span>
                </span>
                <span className="sports-card-title">{event.title}</span>
                <span className="label truncate">{channelLine(event.feeds)}</span>
                <span className="label">Started {fmtTime(event.start)}</span>
              </Focusable>
          })}
        </FocusGroup> : <p className="sports-quiet" role="status">No games are live right now.{ahead[0] ? ` Next: ${ahead[0].title} ${dayOf(ahead[0].start) === today ? '' : `${dayLabel(dayOf(ahead[0].start), today).toLowerCase()} `}at ${fmtTime(ahead[0].start)}.` : ''}</p>}
      </section>}
      <section className="sports-section" aria-label="Coming up" data-testid="sports-upcoming">
        <h2 className="heading">Coming up</h2>
        {!days.length && <p className="sports-quiet" role="status">{date === today ? 'Nothing more is listed for today or tomorrow.' : `No games are listed for ${dayLabel(date, today)}.`}{sport ? ' Try All sports.' : ''}</p>}
        {days.map((day) => <FocusGroup key={day} focusKey={`sports:day-list:${day}`} className="sports-list" role="group" label={dayLabel(day, today)}>
          <h3 className="label sports-day-head">{dayLabel(day, today)}</h3>
          {ahead.filter((event) => dayOf(event.start) === day).map((event) => <Focusable key={event.id} focusKey={cardKey(event)} className={cn('sports-row fx-flat', event.matchup && 'sports-row-matchup')} data-testid="sports-listing" data={{ kind: 'upcoming' }}
            label={`${event.matchup ? matchupTitle(event.matchup) : event.title}, ${event.sport}, ${dayLabel(day, today)} at ${fmtTime(event.start)}`} onEnter={() => open(event)}>
            <span className="sports-row-time">{fmtTime(event.start)}</span>
            {event.matchup && <MatchupArt matchup={event.matchup} className="matchup-thumb" />}
            <span className="sports-row-main">
              <span className="sports-row-title truncate">{event.matchup ? <MatchupTitle matchup={event.matchup} /> : event.title}</span>
              <span className="label truncate">{event.sport} · {channelLine(event.feeds)}</span>
            </span>
            <RecordingBadge state={recordingFor(recordings.data?.recordings, event)?.state} short />
          </Focusable>)}
        </FocusGroup>)}
      </section>
      </>}
      {data.total > PAGE && <FocusGroup focusKey="sports:pages" className="flex gap-3 items-center flex-wrap">
        <Button focusKey="sports:prev" disabled={!page} onEnter={() => setPage(page - 1)}>Previous</Button>
        <span className="label">Page {page + 1} of {Math.ceil(data.total / PAGE)}</span>
        <Button focusKey="sports:next" disabled={(page + 1) * PAGE >= data.total} onEnter={() => setPage(page + 1)}>Next</Button>
      </FocusGroup>}
      {data.truncated && <p className="label" role="status">This is a partial list. Pick a sport or search to narrow it.</p>}
    </>}
    {(gameChannels.length > 0 || !!networks.data?.channels.length) && <section className="sports-section" aria-label="Sports channels" data-testid="sports-channels">
      <h2 className="heading">Sports channels</h2>
      <FocusGroup focusKey="sports:channels" className="row-scroll sports-tiles sports-chan-row" label="Sports channels">
        {gameChannels.map((event) => <Focusable key={event.id} focusKey={cardKey(event)} className="sports-chan fx-flat" data-testid="sports-channel" data={{ kind: 'game' }}
          label={`Watch ${event.title}, ${channelLine(event.feeds)}`} onEnter={() => open(event)}>
          <LogoBlock name={event.league ?? event.title} />
          <span className="sports-row-main">
            <span className="sports-row-title truncate">{event.title}</span>
            <span className="label truncate">{channelLine(event.feeds)}</span>
          </span>
          {event.feeds.some((feed) => feed.quality) && <span className="badge">{event.feeds.find((feed) => feed.quality)!.quality}</span>}
        </Focusable>)}
        {networks.data?.channels.map((channel: Network) => <Focusable key={channel.channel_id} focusKey={`sports:channel:${channel.channel_id}`} className="sports-chan fx-flat" data-testid="sports-channel" data={{ kind: 'network' }}
          label={`Watch ${channel.channel_name}${channel.now ? `, now ${channel.now.title}` : ''}`} onEnter={() => void watch(channel.channel_id)}>
          <LogoBlock name={channel.channel_name} logo={channel.logo} />
          <span className="sports-row-main">
            <span className="sports-row-title truncate">{channel.channel_name}</span>
            <span className="label truncate">Ch {channel.channel_number}{channel.now ? ` · Now: ${channel.now.title}` : ''}</span>
          </span>
          {channel.quality && <span className="badge">{channel.quality}</span>}
        </Focusable>)}
      </FocusGroup>
      {(networks.data?.total ?? 0) > NETWORKS && <p className="label">Showing {NETWORKS} of {networks.data!.total.toLocaleString()} sports channels. Search to find another, or browse them in Live TV.</p>}
    </section>}
    <SportsLibrary language={languages} sport={sport} term={term} />

    <Modal open={filtersOpen} onClose={() => setFiltersOpen(false)} title="Search and language" firstFocusKey="sports:search" variant="sheet" testId="sports-filter-panel">
      <FocusableInput focusKey="sports:search" label="Search games, teams or channels" value={search} onChange={setSearch} placeholder="Team, game or channel" />
      <LanguageFilter scope="sports" value={language} onChange={setLanguage} />
      {(saved.error || saved.saving) && <p className="label" role="status">{saved.error ?? 'Saving…'}</p>}
      <FocusGroup focusKey="sports:filters:actions" className="flex gap-2 flex-wrap">
        <Button focusKey="sports:filters:done" primary onEnter={() => setFiltersOpen(false)}>Done</Button>
        {search && <Button focusKey="sports:filters:clear" ghost onEnter={() => setSearch('')}>Clear search</Button>}
      </FocusGroup>
    </Modal>
    <Modal open={!!picker} onClose={() => setPicker(null)} title={picker?.title ?? ''} firstFocusKey={picker ? `sports:feed:${picker.feeds[0]?.channel_id}` : 'sports:feeds:close'} variant="sheet" testId="sports-picker">
      {picker && <>
        <p className="muted">{picker.kind === 'channel' ? picker.sport : `${picker.sport} · Started ${fmtTime(picker.start)}`} · Choose a channel</p>
        <FocusGroup focusKey="sports:feeds" className="sports-list" role="group" label="Channels">
          {picker.feeds.map((feed) => <Focusable key={feed.channel_id} focusKey={`sports:feed:${feed.channel_id}`} className="sports-feed fx-flat" data-testid="sports-feed"
            label={`Watch on ${feed.channel_name}`} onEnter={() => void watch(feed.channel_id, picker)}>
            <span className="sports-row-main">
              <span className="sports-row-title truncate">{feed.channel_name}</span>
              <span className="label truncate">{[feed.channel_number ? `Ch ${feed.channel_number}` : null, feed.language ? LANG_NAMES[feed.language] ?? feed.language : null].filter(Boolean).join(' · ')}</span>
            </span>
            {feed.quality && <span className="badge">{feed.quality}</span>}
          </Focusable>)}
        </FocusGroup>
        <Button focusKey="sports:feeds:close" className="fx-flat" ghost onEnter={() => setPicker(null)}>Close</Button>
      </>}
    </Modal>
    {sheet && <EventSheet event={sheet} today={today} now={now} recordable={recordable} recordings={recordings.data?.recordings} onClose={() => setSheet(null)}
      onWatch={() => { const event = sheet; setSheet(null); if (event.feeds.length === 1) void watch(event.feeds[0]!.channel_id); else setPicker(event) }} />}
  </div>
}

/** A game as a 16:9 tile: the matchup card when both sides are known, else its title over the sport's tint. */
function GameTile({ event, now, today, recordings, onOpen }: { event: Event; now: number; today: string; recordings: Recording[] | undefined; onOpen: (event: Event) => void }) {
  const on = isLive(event, now)
  const day = dayLabel(dayOf(event.start), today)
  const quality = event.feeds.find((feed) => feed.quality)?.quality
  const badge: ReactNode = <span className="sports-tile-badges">
    {on
      ? <span className={event.live_evidence ? 'badge sports-live' : 'badge'}>{event.replay ? 'Replay' : event.live_evidence ? 'LIVE' : 'On now'}</span>
      : <span className="badge sports-when">{day}</span>}
    {quality && <span className="badge sports-when">{quality}</span>}
    <RecordingBadge state={recordingFor(recordings, event)?.state} short />
  </span>
  const channel = channelLine(event.feeds)
  const where = event.feeds.length === 1 ? `on ${event.feeds[0]!.channel_name}` : `${event.feeds.length} channels`
  const label = on
    ? `Watch ${event.matchup ? matchupSpeech(event, event.matchup) : event.title}, ${event.sport}, ${event.replay ? 'replay' : event.live_evidence ? 'live' : 'on now'}, ${where}`
    : `${event.matchup ? matchupTitle(event.matchup) : event.title}, ${event.sport}, ${day} at ${fmtTime(event.start)}, ${where}`
  return <Focusable focusKey={cardKey(event)} className={cn('sports-tile fx-flat', event.matchup ? 'sports-matchup' : 'sports-tile-plain')} data-testid="sports-listing"
    data={{ kind: on ? 'live' : 'upcoming', matchup: !!event.matchup }} label={label} onEnter={() => onOpen(event)}
    style={event.matchup ? undefined : { '--sport-hue': hueOf(event.sport) } as CSSProperties}>
    {event.matchup ? <MatchupCard event={event} matchup={event.matchup} badge={badge} channel={channel} /> : <>
      <span className="matchup-top">{badge}<span className="matchup-channel truncate">{channel}</span></span>
      <span className="sports-tile-title">{event.title}</span>
      <span className="sports-tile-meta truncate">{on ? `Started ${fmtTime(event.start)}` : fmtTime(event.start)} · {event.sport}</span>
    </>}
  </Focusable>
}

/** A game still to come: when it is on, and (where recording is on and allowed) this game or every game of a side. */
function EventSheet({ event, today, now, recordable, recordings, onClose, onWatch }: {
  event: Event; today: string; now: number; recordable: boolean; recordings: Recording[] | undefined; onClose: () => void; onWatch: () => void
}) {
  const toast = useUi((s) => s.toast)
  const schedule = useScheduleRecording()
  const cancel = useDeleteRecording()
  const createRule = useCreateRecordingRule()
  const [error, setError] = useState<string | null>(null)
  const feed = event.feeds[0]
  const existing = recordingFor(recordings, event)
  const active = existing?.state === 'scheduled' || existing?.state === 'recording'
  const started = isLive(event, now)
  const busy = schedule.isPending || cancel.isPending || createRule.isPending
  // Sides in the order the title names them.
  const sides = [event.away, event.home].filter((side): side is string => !!side).sort((a, b) => event.title.indexOf(a) - event.title.indexOf(b))
  const done = (text: string) => { toast(text, 'success'); onClose() }
  const failed = (e: Error) => setError(e.message)
  const one = () => {
    if (busy || !feed) return
    setError(null)
    schedule.mutate({ programme_id: feed.programme_id, channel_id: feed.channel_id, start: event.start }, { onSuccess: (r) => done(r.state === 'recording' ? `Recording ${r.title} now.` : `${r.title} will be recorded at ${fmtTime(r.starts_at)}.`), onError: failed })
  }
  const stop = () => {
    if (busy || !existing) return
    setError(null)
    cancel.mutate(existing.id, { onSuccess: () => done(`${existing.title} will not be recorded.`), onError: failed })
  }
  const everyGame = (team: string) => {
    if (busy) return
    setError(null)
    createRule.mutate({ kind: 'team', match: team, channel_id: null, new_only: false, keep: null }, { onSuccess: () => done(`Every game for ${team} will be recorded.`), onError: failed })
  }
  const first = started ? 'sports:sheet:watch' : !recordable || !feed ? 'sports:sheet:close' : active ? 'sports:sheet:cancel' : 'sports:sheet:record'
  return <Modal open onClose={onClose} title={event.title} firstFocusKey={first} variant="sheet" testId="sports-sheet">
    {event.matchup && <span className="sports-sheet-card sports-matchup" data-testid="sports-sheet-card">
      <MatchupCard event={event} matchup={event.matchup} badge={<span className="badge">{started ? 'On now' : dayLabel(dayOf(event.start), today)}</span>} channel={channelLine(event.feeds)} />
    </span>}
    <p className="sports-sheet-when">{started ? `Started ${fmtTime(event.start)}` : `${dayLabel(dayOf(event.start), today)} · ${fmtTime(event.start)}`}<RecordingBadge state={existing?.state} /></p>
    <p className="muted truncate">{event.sport} · {channelLine(event.feeds)}</p>
    {error && <p role="alert" data-testid="sports-sheet-error" style={{ color: 'var(--t-danger)' }}>{error}</p>}
    <FocusGroup focusKey="sports:sheet" className="flex flex-col gap-2">
      {started && <Button focusKey="sports:sheet:watch" className="fx-flat" primary icon="play" onEnter={onWatch}>Watch now</Button>}
      {recordable && feed && (active
        ? <Button focusKey="sports:sheet:cancel" className="fx-flat" danger icon="x" disabled={busy} onEnter={stop} testId="sports-record-cancel">{existing?.state === 'recording' ? 'Stop recording' : 'Do not record this game'}</Button>
        : <Button focusKey="sports:sheet:record" className="fx-flat" primary={!started} icon="record" disabled={busy} onEnter={one} testId="sports-record">Record this game</Button>)}
      {recordable && sides.map((team, i) => <Button key={team} focusKey={`sports:sheet:team:${i}`} className="fx-flat" disabled={busy} label={`Record every game for ${team}`} onEnter={() => everyGame(team)} testId="sports-record-team">
        <span className="truncate">Record every {team} game</span>
      </Button>)}
      <Button focusKey="sports:sheet:close" className="fx-flat" ghost onEnter={onClose}>Close</Button>
    </FocusGroup>
  </Modal>
}

/** Sports videos in the household's own library. They open on their title page, where a source is chosen like any other. */
function SportsLibrary({ language, sport, term }: { language: string; sport: string; term: string }) {
  const profile = useSession((s) => s.profile)
  const go = useGo()
  const [page, setPage] = useState(0)
  const filter = `${profile?.id}|${language}|${sport}|${term}`
  const [previousFilter, setPreviousFilter] = useState(filter)
  if (previousFilter !== filter) { setPreviousFilter(filter); setPage(0) }
  const query = useQuery({
    queryKey: ['sports', profile?.id, 'recordings', filter, page],
    enabled: !!profile,
    queryFn: ({ signal }) => unwrap<components['schemas']['SportsRecordingsPage']>(api.GET('/sports/recordings', {
      signal, params: { query: { language, sport: sport || undefined, q: term || undefined, limit: 40, offset: page * 40 } },
    })),
    staleTime: 15_000, refetchInterval: 60_000,
  })
  const data = query.data
  if (data && page > Math.max(0, Math.ceil(data.total / 40) - 1)) setPage(Math.max(0, Math.ceil(data.total / 40) - 1))
  if (!data?.items.length) return null
  return <section className="sports-section" data-testid="sports-recordings" aria-label="From your library">
    <h2 className="heading">From your library</h2>
    <FocusGroup focusKey="sports:recordings" className="row-scroll sports-tiles sports-chan-row" label="Sports videos in your library">
      {data.items.map((item) => {
        // Only an item that names its own source can be opened: nothing is matched by title.
        const exact = !!item.source_id && item.title_id.startsWith(`lib:${item.source_id}:`) && item.title_id.length > item.source_id.length + 5
        const meta = [item.sport, item.source_name, item.originally_available_at ? `Released ${item.originally_available_at}` : null].filter(Boolean).join(' · ')
        const body = <span className="sports-row-main"><span className="sports-row-title truncate">{item.title}</span><span className="label truncate">{meta}</span></span>
        return exact
          ? <Focusable key={item.title_id} focusKey={`sports:recording:${item.title_id}`} className="sports-chan fx-flat" data-testid="sports-recording" label={`Open ${item.title}`} onEnter={() => go(`/title/${encodeURIComponent(item.title_id)}?choose=1`)}>{body}<span className="badge">Library</span></Focusable>
          : <div key={item.title_id} className="sports-chan" data-testid="sports-recording" aria-disabled="true">{body}<span className="badge">Not playable</span></div>
      })}
    </FocusGroup>
    {data.total > 40 && <FocusGroup focusKey="sports:recordings:pages" className="flex flex-wrap gap-3 items-center">
      <Button focusKey="sports:recordings:prev" disabled={!page} onEnter={() => setPage(page - 1)}>Previous</Button>
      <span className="label">Page {page + 1} of {Math.ceil(data.total / 40)}</span>
      <Button focusKey="sports:recordings:next" disabled={(page + 1) * 40 >= data.total} onEnter={() => setPage(page + 1)}>Next</Button>
    </FocusGroup>}
  </section>
}
