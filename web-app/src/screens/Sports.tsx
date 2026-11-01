import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { usePlayAction } from '../components/playAction'
import { CatalogPicker } from '../components/CatalogPicker'
import { LanguageFilter } from '../components/LanguageFilter'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
import { useSession } from '../store/session'
import { LANG_NAMES, useDebounced, useNow } from '../lib/util'

type Event = components['schemas']['SportsEvent']
type EventsPage = components['schemas']['SportsEventsPage']
type Status = 'all' | 'current' | 'upcoming' | 'replay'
const KNOWN_SPORTS = ['American football', 'Basketball', 'Baseball', 'Hockey', 'Cricket', 'Soccer', 'Football', 'Tennis', 'Golf', 'Motorsport', 'Rugby', 'Combat sports', 'Sports']
const PAGE = 40
const ZONE = 'America/New_York'
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const moveDate = (date: string, delta: number) => new Date(Date.parse(`${date}T12:00:00Z`) + delta * 86400000).toISOString().slice(0, 10)
const clock = (date: string, zone: string) => new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: 'numeric', minute: '2-digit' }).format(new Date(date))
const airing = (event: Event, now: number) => Date.parse(event.start) <= now && now < Date.parse(event.stop)
const stateOf = (event: Event, now: number) => airing(event, now) ? 'On now' : Date.parse(event.start) > now ? 'Upcoming' : 'Ended'
const filterSchema = z.object({ language: z.string().max(256), sport: z.string().max(256) })
const validateFilters = (raw: unknown) => { const parsed = filterSchema.safeParse(raw); return parsed.success ? parsed.data : null }

export function Sports() {
  const profileId = useSession((s) => s.profile?.id)
  return <SportsEvents key={profileId} />
}

function SportsEvents() {
  const profile = useSession((s) => s.profile)
  const saved = useProfilePreference('sportsFilters.v1', { language: profile?.languages.length ? 'mine' : 'eng', sport: '' }, validateFilters)
  const { language, sport } = saved.value
  const setLanguage = (language: string) => saved.update((v) => ({ ...v, language }))
  const setSport = (sport: string) => saved.update((v) => ({ ...v, sport }))
  const [search, setSearch] = useHistoryState('sports:search', '')
  const [date, setDate] = useHistoryState('sports:date', today())
  const [status, setStatus] = useHistoryState<Status>('sports:status', 'all')
  const [page, setPage] = useHistoryState('sports:page', 0)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [interacted, setInteracted] = useState(false)
  useEffect(() => {
    const mark = () => setInteracted(true)
    window.addEventListener('keydown', mark, true)
    window.addEventListener('pointerdown', mark, true)
    window.addEventListener('pointermove', mark, true)
    return () => { window.removeEventListener('keydown', mark, true); window.removeEventListener('pointerdown', mark, true); window.removeEventListener('pointermove', mark, true) }
  }, [])
  const [selected, setSelected] = useState<Event | null>(null)
  const term = useDebounced(search, 300)
  const now = useNow(1000)
  const selectedAiring = !!selected && airing(selected, now)
  useEffect(() => { if (selected && !selectedAiring) setFocus('sports:feeds:cancel') }, [selected, selectedAiring])
  const filter = `${profile?.id}|${profile?.languages.join(',')}|${language}|${sport}|${term}|${date}|${status}`
  const [previousFilter, setPreviousFilter] = useState(filter)
  if (previousFilter !== filter) { setPreviousFilter(filter); setPage(0); setSelected(null) }
  const q = useQuery({
    queryKey: ['sports', profile?.id, 'events', filter, page],
    enabled: saved.ready && !!profile,
    queryFn: ({ signal }) => unwrap<EventsPage>(api.GET('/sports/events', { signal, params: { query: {
      date, timezone: ZONE, status, language: language === 'mine' ? profile?.languages.join(',') || 'all' : language,
      sport: sport || undefined, q: term || undefined, limit: PAGE, offset: page * PAGE,
    } } })),
    staleTime: 15_000, refetchInterval: 30_000,
  })
  const play = usePlayAction()
  const data = q.data
  const zone = data?.timezone ?? ZONE
  useEffect(() => {
    if (!saved.ready || data || interacted) return
    const frame = requestAnimationFrame(() => setFocus('sports:filters'))
    return () => cancelAnimationFrame(frame)
  }, [saved.ready, data, interacted])
  const firstEvent = ['On now', 'Upcoming', 'Ended'].flatMap((section) => data?.events.filter((event) => stateOf(event, now) === section) ?? [])[0]
  useRestoreFocus(saved.ready && !!data && !interacted, firstEvent ? `sports:event:${firstEvent.id}` : 'sports:filters')
  if (data && page > Math.max(0, Math.ceil(data.total / PAGE) - 1)) setPage(Math.max(0, Math.ceil(data.total / PAGE) - 1))
  const close = () => setSelected(null)
  if (!saved.ready) return <div className="page-pad" data-testid="sports"><h1 className="display">Sports</h1>{saved.error ? <><p role="alert">{saved.error}</p><Button focusKey="sports:retry-prefs" onEnter={() => void saved.retry()}>Retry</Button></> : <p role="status">Loading saved filters…</p>}</div>
  return <div className="page-pad" data-testid="sports">
    <h1 className="display mb-2">Sports</h1>
    <p className="label mb-3">Guide broadcasts · On now does not necessarily mean a live match.</p>
    <div className="sports-compact-toolbar">
      <Button focusKey="sports:filters" onEnter={() => setFiltersOpen(true)} testId="sports-filters">Filters</Button>
      <p className="label sports-filter-summary" data-testid="sports-filter-summary">{language === 'mine' ? (profile?.languages.map((code) => LANG_NAMES[code] ?? code).join(', ') || 'All languages') : language === 'all' ? 'All languages' : LANG_NAMES[language] ?? language} · {sport || 'All sports'} · {date === today() ? 'Today' : date}{search ? ` · “${search}”` : ''}</p>
    </div>
      <FocusGroup focusKey="sports:status" className="flex flex-wrap gap-2" label="Broadcast status">
        {([['all', 'All broadcasts'], ['current', 'On now'], ['upcoming', 'Upcoming'], ['replay', 'Replays']] as const).map(([value, label]) => <Chip key={value} focusKey={`sports:status:${value}`} pressed={status === value} onEnter={() => setStatus(value)}>{label}</Chip>)}
      </FocusGroup>
    {saved.error && <p role="alert">{saved.error}</p>}
    {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : q.isPending ? <Skeleton style={{ height: '16rem' }} /> : data && <>
      {data.truncated && <p role="status">Guide scan limit reached. Counts are a lower bound; narrow the date, sport or search for more precise results.</p>}
      {!data.events.length ? <Empty title="No matching sports listings" hint="Try All sports, another day or language. No listings does not mean no sport is airing." /> : ['On now', 'Upcoming', 'Ended'].map((section) => {
        const events = data.events.filter((event) => stateOf(event, now) === section)
        return events.length > 0 && <section key={section} className="sports-events-section" aria-label={section}>
          <h2 className="heading mb-3">{section === 'Ended' ? 'Earlier broadcasts' : section}</h2>
          <FocusGroup focusKey={`sports:rail:${section}`} className="sports-event-rail" label={`${section} sports events`}>
            {events.map((event) => <article key={event.id} data-testid="sports-listing" className="sports-event-item">
              <Focusable focusKey={`sports:event:${event.id}`} className="sports-event-card fx-flat" label={`View feeds for ${event.title}`} onEnter={() => setSelected(event)}>
                <div className="sports-event-header"><span className={event.live_evidence && !event.replay && airing(event, now) ? 'badge sports-live-badge' : 'badge'}>{event.replay ? 'Replay' : event.live_evidence && airing(event, now) ? 'LIVE · guide label' : section}</span><span className="label">{event.sport}</span></div>
                <h3 className="sports-event-title">{event.title}</h3>
                <div className="sports-event-footer"><span>{clock(event.start, zone)}</span><span>{event.feeds.length ? `${event.feeds.length} ${event.feeds.length === 1 ? 'feed' : 'feeds'}` : 'No feed'}</span></div>
              </Focusable>
              <p className="label sports-event-caption">{event.feeds.length === 1 ? event.feeds[0]!.channel_name : `${event.feeds.length} channel feeds`}<br />{event.sport}</p>
            </article>)}
          </FocusGroup>
        </section>
      })}
      <div className="flex gap-3 items-center flex-wrap mt-4">
        <Button focusKey="sports:prev" disabled={!page} onEnter={() => setPage(page - 1)}>Previous</Button>
        <span>{data.truncated ? 'At least ' : ''}{data.total.toLocaleString()} events · page {page + 1} of {Math.max(1, Math.ceil(data.total / PAGE))}</span>
        <Button focusKey="sports:next" disabled={(page + 1) * PAGE >= data.total} onEnter={() => setPage(page + 1)}>Next</Button>
      </div>
      <p className="muted mb-4" role="status">{data.coverage.channels_with_sports_schedule.toLocaleString()} of {data.coverage.eligible_channels.toLocaleString()} eligible channels have sports in the inspected guide window. Missing or unclassified guide data may omit events. Updated {clock(data.as_of, zone)}.</p>
    </>}
    <Modal open={filtersOpen} onClose={() => setFiltersOpen(false)} title="Sports filters" firstFocusKey="sports:search" wide>
      <div className="flex flex-col gap-4">
      <FocusableInput focusKey="sports:search" label="Search sports programmes or channels" value={search} onChange={setSearch} placeholder="Event, programme or channel" />
      <LanguageFilter scope="sports" value={language} onChange={setLanguage} />
      <FocusGroup focusKey="sports:dates" className="flex flex-wrap gap-2 items-center" label="Sports date">
        <Button focusKey="sports:day:prev" onEnter={() => setDate(moveDate(date, -1))}>Previous day</Button>
        <Button focusKey="sports:day:today" onEnter={() => setDate(today())}>Today</Button>
        <Button focusKey="sports:day:next" onEnter={() => setDate(moveDate(date, 1))}>Next day</Button>
        <span className="label" data-testid="sports-date">{date} · {zone}</span>
      </FocusGroup>

      <FocusGroup focusKey="sports:types" className="flex flex-wrap gap-2" label="Sport type">
        <Chip focusKey="sports:all" pressed={!sport} onEnter={() => setSport('')}>All sports</Chip>
        <CatalogPicker scope="sports:sport-picker" label="Sport" value={sport} selectedLabel={sport || 'All sports'} options={[...new Set([...KNOWN_SPORTS, ...(data?.sports.map((entry) => entry.name) ?? [])])].map((name) => ({ id: name, label: name, count: data?.sports.find((entry) => entry.name === name)?.count }))} onChange={setSport} />
        {(data?.sports ?? []).map((kind) => <Chip key={kind.name} focusKey={`sports:type:${kind.name}`} pressed={sport === kind.name} onEnter={() => setSport(kind.name)}>{kind.name} · {kind.count}</Chip>)}
        {sport && !data?.sports.some((kind) => kind.name === sport) && <Chip focusKey="sports:selected" pressed onEnter={() => setSport('')}>{sport} · Clear</Chip>}
      </FocusGroup>

        <p className="label" role="status">{saved.error ?? (saved.saving ? 'Saving filters…' : 'Language and sport sync with this profile.')}</p>
        <p className="label">Guide listings include games, replays, highlights and studio shows. Missing metadata may omit events. Channel language hints do not verify spoken audio.</p>
        <Button focusKey="sports:filters:done" onEnter={() => setFiltersOpen(false)}>Show events</Button>
      </div>
    </Modal>
    <Modal open={!!selected} onClose={close} title={selected?.title ?? 'Channel feeds'} firstFocusKey="sports:feeds:cancel" wide>
      {selected && <>
        <p>{stateOf(selected, now)}{selected.replay ? ' · Replay' : ''} · {clock(selected.start, zone)}–{clock(selected.stop, zone)} · {zone}</p>
        <p className="label">Feeds are matched by guide title and broadcast time. Audio language is unverified unless explicitly marked verified; a channel language hint is not proof of spoken audio. Choosing Watch explicitly accepts that feed’s audio.</p>
        {!airing(selected, now) && <p>{Date.parse(selected.start) > now ? 'Scheduled for later. Playback will be offered while this broadcast is airing.' : 'This broadcast has ended. Replay playback availability is not verified.'}</p>}
        {selected.feeds.map((feed) => <div key={`${feed.channel_id}:${feed.programme_id}`} className="sports-feed-option">
          <p><strong>{feed.channel_name}</strong> · {feed.source_name}</p>
          <p className="label">{feed.language_verified ? `Verified audio: ${LANG_NAMES[feed.language ?? ''] ?? feed.language ?? 'Unknown'}` : `Audio unverified${feed.language ? ` · Channel hint: ${LANG_NAMES[feed.language] ?? feed.language}` : ''}`}</p>
          {airing(selected, now) && <Button focusKey={`sports:feed:${feed.channel_id}:${feed.programme_id}`} onEnter={() => { if (airing(selected, Date.now())) { setFocus(`sports:event:${selected.id}`); void play({ id: feed.channel_id, kind: 'channel' }, null, { variantId: feed.channel_id, allowUnverifiedAudio: true }) } }}>Watch {feed.channel_name}</Button>}
        </div>)}
        {!selected.feeds.length && <p>No authorized channel feed is available for this listing.</p>}
      </>}
      <Button focusKey="sports:feeds:cancel" onEnter={close}>Back to events</Button>
    </Modal>
  </div>
}
