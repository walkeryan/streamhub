import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { useRecordings, useSearch } from '../api/hooks'
import { useCan } from '../api/me'
import type { Channel, SearchProgramme, SearchResult, SearchSectionName, Tile } from '../api/types'
import { Attribution, Button, Chip, Empty, ErrorState, LogoBlock, Skeleton } from '../components/ui'
import { MediaTile, openPath } from '../components/Tile'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
import { setFocus } from '../nav/spatial'
import { usePhone } from '../lib/layout'
import { MIN_QUERY, SEARCH_BOX, SEARCH_PATH, searchPath, useSearchQuery, useSlashToSearch } from '../lib/searchQuery'
import { fmtTime, useNow } from '../lib/util'
import { RecordDialog } from '../recordings/RecordDialog'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

const HEADINGS: Record<SearchSectionName, string> = { programmes: 'On TV', channels: 'Channels', titles: 'Movies & Shows' }
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

/**
 * The results of the one search box: up to three sections (On TV, Channels, Movies & Shows), each only
 * when it found something, in the order the server gives. Nothing sits above them: the box is in the
 * top bar, and on a phone, which has no top bar, at the top of this page.
 */
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
  const { data, error, isPlaceholderData, refetch } = useSearch(q, expanded)
  // The query the results on screen answer: while the next one loads they stay up, dimmed.
  const [settled, setSettled] = useState<string | null>(null)
  if (asked && data && !isPlaceholderData && settled !== q) setSettled(q)
  const shown = asked ? data : undefined
  const stale = !!shown && isPlaceholderData && settled !== q
  const found = shown ? shown.results.length + shown.channels.length + shown.programmes.length : 0
  const ready = !asked || !!data || !!error
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
    if (!grown || !shown || isPlaceholderData) return
    const rows = shown[FIELD[grown.name]] as Array<SearchProgramme & Channel & Tile>
    const row = rows[Math.min(grown.from, rows.length - 1)]
    setGrown(null)
    if (row) void setFocus((itemKey[grown.name] as (r: typeof row) => string)(row))
  }, [grown, shown, isPlaceholderData])

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

  const section = (name: SearchSectionName, r: SearchResult): ReactNode => {
    const rows = r[FIELD[name]]
    if (!rows.length) return null
    const count = visible(name, r)
    return (
      <section key={name} className="search-section" data-testid={`search-${name}`} aria-label={HEADINGS[name]}>
        <h2 className="heading">{HEADINGS[name]}</h2>
        {name === 'programmes' ? <FocusGroup focusKey="search:programmes" className="search-rows">
          {r.programmes.slice(0, count).map((p) => <BroadcastRow key={p.id} p={p} live={onNow(p)} when={startLabel(p.start, now)} onEnter={() => openBroadcast(p)} />)}
        </FocusGroup> : name === 'channels' ? <FocusGroup focusKey="search:channels" className="search-rows">
          {r.channels.slice(0, count).map((c) => <ChannelResult key={c.id} channel={c} onEnter={() => go(play(c.id))} />)}
        </FocusGroup> : <SearchTitles tiles={r.results} onOpen={(t) => go(openPath(t))} />}
        {(count < rows.length || (r.sections[name].more && !expanded.includes(name))) && <FocusGroup focusKey={`search:more:${name}:group`} className="search-more">
          <Button focusKey={`search:more:${name}`} ghost disabled={isPlaceholderData} testId={`search-more-${name}`} onEnter={() => more(name, r)}>Show more</Button>
        </FocusGroup>}
      </section>
    )
  }

  return (
    <div className="page-pad search-page" data-testid="search">
      {phone && <PageSearchField />}
      <div data-testid="search-results" aria-busy={stale || (asked && !shown && !error) || undefined} data-stale={stale || undefined}>
        {!asked ? <SearchIdle />
          : error && !shown ? <ErrorState error={error} onRetry={() => void refetch()} />
          // Nothing to keep on screen while this query loads (the last one found nothing): skeletons.
          : !shown || (found === 0 && isPlaceholderData) ? <SearchSkeleton />
          : found === 0 ? <Empty title={`No results for “${q}”`} hint="Check the spelling, or try fewer words. Channels are also found by number." />
          : <FocusGroup focusKey="search:results" className="search-sections">
            {shown.order.map((name) => section(name, shown))}
            {shown.results.length > 0 && <Attribution text={shown.attribution} />}
          </FocusGroup>}
      </div>
      <RecordDialog target={recording ? { channel: { id: recording.channel_id, name: recording.channel_name }, programmes: [recording] } : null} onClose={() => setRecording(null)} />
    </div>
  )
}

/**
 * The poster grid of a search. A tile says where its title stands in its badge row: "In your library"
 * (or its sources) for one the household has, the state of its request for one that was asked for,
 * nothing otherwise (a green tick when the title plays here). That row is `MediaTile`'s, drawn from `sources` and `request_state`:
 * the one place to change when request status gets a component of its own. Movie or Series is said
 * only where two results share a name.
 */
function SearchTitles({ tiles, onOpen }: { tiles: Tile[]; onOpen: (t: Tile) => void }) {
  const names = new Map<string, Set<string>>()
  const kindOf = (t: Tile) => (t.kind === 'movie' || t.kind === 'vod' ? 'Movie' : 'Series')
  for (const t of tiles) names.set(t.title.toLowerCase(), (names.get(t.title.toLowerCase()) ?? new Set()).add(kindOf(t)))
  return (
    <FocusGroup focusKey="search:titles" className="grid-tiles search-grid">
      {tiles.map((t) => (
        <div key={t.id} className="search-tile">
          <MediaTile tile={t} scope="search" titleAlways mark="availability" onOpen={onOpen} />
          {names.get(t.title.toLowerCase())!.size > 1 && <span className="badge search-kind" data-testid="search-kind">{kindOf(t)}</span>}
        </div>
      ))}
    </FocusGroup>
  )
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

/** A phone has no top bar: the same search box sits at the top of the results. */
function PageSearchField() {
  const box = useSearchQuery()
  useSlashToSearch(() => { void setFocus(SEARCH_BOX.page) })
  // Escape clears first; with nothing typed it is Back, which leaves search.
  const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape' && box.text) { e.preventDefault(); box.clear() } }
  return (
    <div className="search-page-field" role="search" onKeyDown={onKeyDown}>
      <FocusableInput focusKey={SEARCH_BOX.page} label="Search" placeholder="Search" value={box.text} onChange={box.setText} inputMode="search" onSubmit={box.submit} />
    </div>
  )
}

/** Before anything is typed: what was searched for lately, and the way to titles nobody has yet. */
function SearchIdle() {
  const go = useGo()
  const recents = useUi((s) => s.recentSearches)
  return (
    <FocusGroup focusKey="search:idle" className="search-idle" data-testid="search-idle">
      <p className="muted">Movies, shows, channels and what is on TV.</p>
      {recents.length > 0 && <FocusGroup focusKey="search:recent" className="flex gap-2 flex-wrap" role="group" label="Recent searches">
        {recents.map((r) => <Chip key={r} focusKey={`search:recent:${r}`} onEnter={() => go(searchPath(r), { replace: true })}>{r}</Chip>)}
      </FocusGroup>}
      <FocusGroup focusKey="search:discover" className="flex gap-2 flex-wrap">
        <Button focusKey="search:discover:movies" onEnter={() => go('/discover/movies')}>Popular movies</Button>
        <Button focusKey="search:discover:shows" onEnter={() => go('/discover/shows')}>Popular shows</Button>
      </FocusGroup>
    </FocusGroup>
  )
}

function SearchSkeleton() {
  return (
    <div className="search-sections" data-testid="search-loading">
      <div className="search-section"><Skeleton style={{ width: '6rem', height: '1.5rem' }} />
        <div className="search-rows">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} style={{ height: '3.5rem' }} />)}</div>
      </div>
      <div className="search-section"><Skeleton style={{ width: '10rem', height: '1.5rem' }} />
        <div className="grid-tiles search-grid">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} style={{ aspectRatio: '2/3' }} />)}</div>
      </div>
    </div>
  )
}
