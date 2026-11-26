import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { searchView, useRecordings, useSearch } from '../api/hooks'
import { useCan } from '../api/me'
import type { Channel, SearchProgramme, SearchResult, SearchSectionName, Tile } from '../api/types'
import { Attribution, LogoBlock } from '../components/ui'
import { Button, Chip, Empty, ErrorState, Icon, SectionHeader, Skeleton } from '../ds'
import { MediaTile, openPath } from '../components/Tile'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
import { setFocus } from '../nav/spatial'
import { usePhone } from '../lib/layout'
import { MIN_QUERY, SEARCH_BOX, SEARCH_PATH, searchPath } from '../lib/searchQuery'
import { fmtTime, useNow } from '../lib/util'
import { RecordDialog } from '../recordings/RecordDialog'
import { SearchBox } from '../components/SearchBox'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

const FIELD: Record<SearchSectionName, 'programmes' | 'channels' | 'results'> = { programmes: 'programmes', channels: 'channels', titles: 'results' }
const itemKey = { programmes: (p: SearchProgramme) => `search-prog::${p.id}`, channels: (c: Channel) => `search-ch::${c.id}`, titles: (t: Tile) => `search::${t.id}` }
const play = (channelId: string) => `/play/${encodeURIComponent(channelId)}`

/** "Today 8:30 PM", "Tomorrow 9:00 AM", "Sat 7:00 PM": the viewer's own clock. */
function startLabel(iso: string, now: number): string {
  const start = new Date(iso), today = new Date(now)
  const days = Math.round((new Date(start.getFullYear(), start.getMonth(), start.getDate()).getTime() - new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()) / 86_400_000)
  const day = days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : start.toLocaleDateString([], { weekday: 'short' })
  return `${day} ${fmtTime(start)}`
}

// Where each results page was scrolled to, so Back from a title returns to the same place.
const scrolls = new Map<string, number>()
// A query in the address when the profile changes was the last viewer's: the next one starts with an empty box.
let left: string | null = null
useSession.subscribe((session, previous) => {
  if (session.profile?.id === previous.profile?.id) return
  scrolls.clear()
  left = window.location.pathname === SEARCH_PATH ? window.location.search : null
})

export function Search() {
  const profileId = useSession((s) => s.profile?.id ?? 'anonymous')
  const loc = useLocation()
  const navigate = useNavigate()
  const stale = !!loc.search && left === loc.search
  useEffect(() => { if (stale) { left = null; navigate(SEARCH_PATH, { replace: true }) } }, [stale, navigate])
  return stale ? null : <SearchPage key={profileId} />
}

function SearchPage() {
  const [params] = useSearchParams()
  const loc = useLocation()
  const q = (params.get('q') ?? '').trim()
  const asked = q.length >= MIN_QUERY
  const phone = usePhone()
  const leave = useGo()
  const toast = useUi((s) => s.toast)
  const addSearch = useUi((s) => s.addSearch)
  // A query that led somewhere is worth offering again; the letters typed on the way to it are not.
  const go = (to: string) => { addSearch(q); leave(to) }
  // "Show more": the sections shown in full, and those of them the server was asked for in full.
  const [opened, setOpened] = useHistoryState<SearchSectionName[]>('search:opened', [])
  const [expanded, setExpanded] = useHistoryState<SearchSectionName[]>('search:expanded', [])
  // Every query is asked twice. What the hub holds answers at once and is shown as soon as it lands;
  // what the metadata provider knows comes when it comes, and its titles join the end of the grid.
  const local = useSearch(q, expanded, false)
  const full = useSearch(q, expanded, true)
  const current = useMemo(() => (local.data ? searchView(local.data, full.data) : local.error ? full.data : undefined), [local.data, local.error, full.data])
  const error = local.error && full.error
  // The answer on screen: while the next query (or a fuller page of this one) loads, the last stays up,
  // dimmed when it answers another query. An answer to an older query never replaces a newer one.
  const [last, setLast] = useState<{ q: string; view: SearchResult } | null>(null)
  if (asked && current && last?.view !== current) setLast({ q, view: current })
  const shown = asked ? (current ?? last?.view) : undefined
  const loading = !current
  const stale = loading && !!shown && last?.q !== q
  const found = shown ? shown.results.length + shown.channels.length + shown.programmes.length + (shown.actors?.length ?? 0) + (shown.services?.length ?? 0) : 0
  // More titles may still be on their way: the page says so where they will go, and is not "empty" yet.
  const discovering = !!local.data && !!local.data.metadata_available && full.isPending
  const ready = !asked || !!shown || !!error
  useRestoreFocus(ready, asked ? 'search:results' : phone ? SEARCH_BOX.page : 'search:idle')

  // A new query starts at the top; Back to these results returns to where they were left.
  const restored = useRef<string | null>(null)
  useEffect(() => {
    if (!ready || restored.current === loc.key) return
    const id = window.setTimeout(() => { restored.current = loc.key; window.scrollTo(0, scrolls.get(loc.key) ?? 0) }, 60)
    return () => window.clearTimeout(id)
  }, [ready, loc.key])
  useEffect(() => {
    const onScroll = () => { if (restored.current === loc.key) { scrolls.set(loc.key, window.scrollY); if (scrolls.size > 100) scrolls.delete(scrolls.keys().next().value!) } }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [loc.key])

  // Broadcasts and channels open on a few rows, so that the posters are not a screen away when a word is
  // on many channels; a section that is all there is to show opens in full. "Show more" shows the rest
  // in place, asking the server for what it held back, and focus moves to the first row that was added.
  const brief = phone ? 3 : 6
  const visible = (name: SearchSectionName, r: SearchResult): number => {
    const rows = r[FIELD[name]].length
    return name === 'titles' || opened.includes(name) || rows === r.results.length + r.channels.length + r.programmes.length ? rows : Math.min(rows, brief)
  }
  const [grown, setGrown] = useState<{ name: SearchSectionName; from: number } | null>(null)
  const more = (name: SearchSectionName, r: SearchResult) => {
    setGrown({ name, from: visible(name, r) })
    setOpened((o) => (o.includes(name) ? o : [...o, name]))
    if (r.sections[name].more) setExpanded((e) => (e.includes(name) ? e : [...e, name]))
  }
  useEffect(() => {
    if (!grown || !shown || loading) return
    const rows = shown[FIELD[grown.name]] as Array<SearchProgramme & Channel & Tile>
    const row = rows[Math.min(grown.from, rows.length - 1)]
    setGrown(null)
    if (row) void setFocus((itemKey[grown.name] as (r: typeof row) => string)(row))
  }, [grown, shown, loading])

  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const [recording, setRecording] = useState<SearchProgramme | null>(null)
  const now = useNow()
  const onNow = (p: SearchProgramme) => Date.parse(p.start) <= now && Date.parse(p.stop) > now
  const openBroadcast = (p: SearchProgramme) => {
    if (onNow(p)) go(play(p.channel_id))
    else if (recordable) setRecording(p)
    else toast(`${p.title} starts ${startLabel(p.start, now)} on ${p.channel_number} ${p.channel_name}.`)
  }

  const moreButton = (name: SearchSectionName, r: SearchResult) => (
    visible(name, r) < r[FIELD[name]].length || (r.sections[name].more && !expanded.includes(name))
  ) && <FocusGroup focusKey={`search:more:${name}:group`} className="search-more">
    <Button focusKey={`search:more:${name}`} className="btn-chip" icon="chevD" disabled={loading}
      testId={`search-more-${name}`} onEnter={() => more(name, r)}>Show more</Button>
  </FocusGroup>
  const movies = shown?.results.filter(t => t.kind === 'movie' || t.kind === 'vod') ?? []
  const series = shown?.results.filter(t => t.kind === 'show' || t.kind === 'series') ?? []
  const broadcasts = shown?.programmes.slice(0, visible('programmes', shown)) ?? []
  const entitySection = (kind: 'actor' | 'service', matches: {id: string; name: string}[] = []) => {
    if (!matches.length) return null
    const heading = kind === 'actor' ? 'Actors' : 'Streaming services'
    return <section className="search-section" aria-label={heading}>
      <SectionHeader title={heading} />
      <FocusGroup focusKey={`search:${kind}`} className="search-rows">
        {matches.map(match => <Focusable key={match.id} focusKey={`search:${kind}:${match.id}`} className="search-row fx-flat"
          label={match.name} role="link" onEnter={() => go(`/search/${kind}/${encodeURIComponent(match.id)}?name=${encodeURIComponent(match.name)}`)}>
          <Icon name={kind === 'actor' ? 'users' : 'tv'} /><strong>{match.name}</strong><Icon name="chevR" />
        </Focusable>)}
      </FocusGroup>
    </section>
  }

  return (
    <div className="page-pad search-page" data-testid="search">
      {phone && <div className="search-page-field"><SearchBox label="Search" page /></div>}
      <div data-testid="search-results" aria-busy={stale || (asked && !shown && !error) || undefined} data-stale={stale || undefined}>
        {!asked ? <SearchIdle />
          : error && !shown ? <ErrorState error={error} onRetry={() => { void local.refetch(); void full.refetch() }} />
          // Nothing to keep on screen while this query loads (the last one found nothing, or the hub
          // has nothing and the provider has not answered): skeletons, not "No results" too soon.
          : !shown || (found === 0 && (loading || discovering)) ? <SearchSkeleton />
          : found === 0 ? <Empty icon="search" title={`No results for “${q}”`} hint="Check the spelling, or try fewer words. Channels are also found by number." />
          : <FocusGroup focusKey="search:results" className="search-sections">
            {(shown.programmes.length > 0 || shown.channels.length > 0) && <section className="search-section" aria-label="Live TV">
              <SectionHeader title="Live TV" />
              {broadcasts.length > 0 && <div data-testid="search-programmes">
                <FocusGroup focusKey="search:programmes" className="search-rows">
                  {broadcasts.map(p => <BroadcastRow key={p.id} p={p} live={onNow(p)} when={startLabel(p.start, now)} onEnter={() => openBroadcast(p)} />)}
                </FocusGroup>
                {moreButton('programmes', shown)}
              </div>}
              {shown.channels.length > 0 && <div data-testid="search-channels">
                <FocusGroup focusKey="search:channels" className="search-rows" label="Channels">
                  {shown.channels.slice(0, visible('channels', shown)).map(c => <ChannelResult key={c.id} channel={c} onEnter={() => go(play(c.id))} />)}
                </FocusGroup>
                {moreButton('channels', shown)}
              </div>}
            </section>}
            {(movies.length > 0 || series.length > 0 || discovering) && <div className="search-title-groups" data-testid="search-titles">
              <TitleGroup title="Movies" tiles={movies} onOpen={t => go(openPath(t))} />
              <TitleGroup title="TV series" tiles={series} onOpen={t => go(openPath(t))} />
              {discovering && <span className="label" role="status" data-testid="search-discovering">Looking for more matches…</span>}
              {moreButton('titles', shown)}
            </div>}
            {entitySection('actor', shown.actors)}
            {entitySection('service', shown.services)}
            {shown.results.length > 0 && <Attribution text={shown.attribution} />}
          </FocusGroup>}
      </div>
      <RecordDialog target={recording ? { channel: { id: recording.channel_id, name: recording.channel_name }, programmes: [recording] } : null} onClose={() => setRecording(null)} />
    </div>
  )
}

function TitleGroup({ title, tiles, onOpen }: { title: string; tiles: Tile[]; onOpen: (t: Tile) => void }) {
  if (!tiles.length) return null
  return <section className="search-section" aria-label={title}>
    <SectionHeader title={title} />
    <FocusGroup focusKey={`search:titles:${title}`} className="grid-tiles search-grid">
      {tiles.map(t => <MediaTile key={t.id} tile={t} scope="search" titleAlways mark="availability" onOpen={onOpen} />)}
    </FocusGroup>
  </section>
}

/** One broadcast: where it is on, its title, and "On now" or the day and time it starts. */
function BroadcastRow({ p, live, when, onEnter }: { p: SearchProgramme; live: boolean; when: string; onEnter: () => void }) {
  const others = p.feeds.length - 1
  const on = `${p.channel_number} · ${p.channel_name}${others > 0 ? ` +${others} more` : ''}`
  return (
    <Focusable focusKey={itemKey.programmes(p)} className="search-row fx-flat" role="link" data-testid="search-broadcast" data={{ live }}
      label={`${p.title}, ${live ? 'on now' : when}, channel ${p.channel_number} ${p.channel_name}`} onEnter={onEnter} scroll="nearest">
      <LogoBlock name={p.channel_name} logo={p.channel_logo} />
      <span className="search-row-copy">
        <span className="search-row-title truncate">{p.title}</span>
        <span className="search-row-line">
          {live ? <span className="badge badge-play">On now</span> : <span className="label search-row-when">{when}</span>}
          <span className="label truncate">{on}</span>
        </span>
      </span>
      <RecordingBadge state={p.recording_state} short />
    </Focusable>
  )
}

/** One channel: logo, number, name, and what it is showing when its guide says. */
function ChannelResult({ channel: c, onEnter }: { channel: Channel; onEnter: () => void }) {
  return (
    <Focusable focusKey={itemKey.channels(c)} className="search-row fx-flat" role="link" data-testid="search-channel"
      label={`${c.number} ${c.name}${c.now ? `, now ${c.now.title}` : ''}`} onEnter={onEnter} scroll="nearest">
      <LogoBlock name={c.name} logo={c.logo} />
      <span className="search-row-copy">
        <span className="search-row-title truncate">{c.name}</span>
        {c.now && <span className="label truncate">{c.now.title}</span>}
      </span>
      <span className="search-row-number">{c.number}</span>
    </Focusable>
  )
}

/** Before anything is typed: what was searched for lately, and the way to titles nobody has yet. */
function SearchIdle() {
  const go = useGo()
  const recents = useUi((s) => s.recentSearches)
  const clearSearches = useUi((s) => s.clearSearches)
  return (
    <FocusGroup focusKey="search:idle" className="search-idle" data-testid="search-idle">
      <div className="search-idle-lead">
        <span className="ds-empty-icon" aria-hidden="true"><Icon name="search" /></span>
        <p className="muted">Search live TV, movies, series, actors and streaming services.</p>
      </div>
      {recents.length > 0 && <div className="search-idle-block">
        <SectionHeader title="Recent searches" action={<Button focusKey="search:recent:clear" ghost icon="x" onEnter={clearSearches} testId="search-recent-clear">Clear</Button>} />
        <FocusGroup focusKey="search:recent" className="lib-chips" role="group" label="Recent searches">
          {recents.map((r) => <Chip key={r} focusKey={`search:recent:${r}`} onEnter={() => go(searchPath(r), { replace: true })}>{r}</Chip>)}
        </FocusGroup>
      </div>}
      <FocusGroup focusKey="search:discover" className="lib-chips">
        <Button focusKey="search:discover:movies" className="btn-chip" icon="film" onEnter={() => go('/discover/movies')}>Popular movies</Button>
        <Button focusKey="search:discover:shows" className="btn-chip" icon="shows" onEnter={() => go('/discover/shows')}>Popular shows</Button>
      </FocusGroup>
    </FocusGroup>
  )
}

function SearchSkeleton() {
  return (
    <div className="search-sections" data-testid="search-loading">
      <div className="search-section"><Skeleton className="ds-skel-title" style={{ width: '6rem' }} />
        <div className="search-rows">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="search-row-skel" />)}</div>
      </div>
      <div className="search-section"><Skeleton className="ds-skel-title" style={{ width: '10rem' }} />
        <div className="grid-tiles search-grid">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} className="ds-skel-tile" style={{ aspectRatio: '2/3' }} />)}</div>
      </div>
    </div>
  )
}
