import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { useChannelGroups, useChannels, useLanguages, useRecordings, useSources, useToggleFavorite } from '../api/hooks'
import type { Channel, ChannelGroup } from '../api/types'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { CatalogPicker } from '../components/CatalogPicker'
import { ChipMenu } from '../components/ChipMenu'
import { LanguageFilter, languageScope } from '../components/LanguageFilter'
import { ChannelRow } from '../components/ChannelRow'
import { Icon } from '../components/Icon'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { doesFocusableExist, setFocus } from '../nav/spatial'
import { useHistoryState } from '../nav/historyState'
import { useGo, useRestoreFocus } from '../nav/back'
import { Preview } from '../player/Preview'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { usePhone, useWide } from '../lib/layout'
import { LANG_NAMES, fmtTime, useDebounced } from '../lib/util'
import { useCan } from '../api/me'
import { RecordDialog } from '../recordings/RecordDialog'
import { RecordingBadge } from '../recordings/RecordingBadge'

const PAGE = 80
// Top-level groups shown as chips; a provider with more keeps the rest behind one searchable picker.
const GROUP_CHIPS = 24
const Guide = lazy(() => import('./Guide').then((m) => ({ default: m.Guide })))

const liveFilterSchema = z.object({
  language: z.string().max(256), country: z.enum(['US', 'all', 'und']),
  group: z.string().max(512), source: z.string().max(256), favorites: z.boolean(),
})
type SavedLiveFilters = z.infer<typeof liveFilterSchema>
// A parent's count already includes its children's.
const channelCount = (groups?: ChannelGroup[]) => groups?.reduce((sum, g) => sum + (g.parent ? 0 : g.channel_count), 0)
const validateLiveFilters = (raw: unknown) => { const parsed = liveFilterSchema.safeParse(raw); return parsed.success ? parsed.data : null }

const PREVIEW_KEY = 'sh.live.preview'

export function LiveTV() {
  const location = useLocation()
  const [view, setView] = useHistoryState<'channels' | 'guide'>('live:view', new URLSearchParams(location.search).get('view') === 'guide' ? 'guide' : 'channels')
  const profile = useSession((s) => s.profile)
  const defaults: SavedLiveFilters = { language: profile?.languages.length ? 'mine' : 'all', country: 'US', group: '', source: '', favorites: false }
  const saved = useProfilePreference('liveFilters.v1', defaults, validateLiveFilters)
  const { language: lang, country, group, source, favorites: favOnly } = saved.value
  const setLang = (language: string) => saved.update((v) => ({ ...v, language, group: '' }))
  const setCountry = (country: SavedLiveFilters['country']) => saved.update((v) => ({ ...v, country, group: '' }))
  // All, Favorites and each group are one row of choices: picking one replaces the others.
  // `group` is the id of the group filtered by, a parent's or a child's: the chip row works out the rest.
  const pick = (group: string, favorites = false) => saved.update((v) => ({ ...v, group, favorites }))
  const setSource = (source: string) => saved.update((v) => ({ ...v, source, group: '' }))
  const [search, setSearch] = useHistoryState('live:search', '')
  const term = useDebounced(search, 300)
  const [page, setPage] = useHistoryState('live:page', 0)
  const [focusedId, setFocusedId] = useHistoryState<string | null>('live:selected-channel', null)
  // Off until the viewer asks: every preview holds one of the source's few connections. The choice
  // is kept for this browser, so someone who wants previews turns them on once.
  const [previewEnabled, setPreviewOn] = useState(() => { try { return localStorage.getItem(PREVIEW_KEY) === '1' } catch { return false } })
  const setPreviewEnabled = (next: boolean | ((was: boolean) => boolean)) => setPreviewOn((was) => {
    const on = typeof next === 'function' ? next(was) : next
    try { localStorage.setItem(PREVIEW_KEY, on ? '1' : '0') } catch { /* private window: the choice lasts for the visit */ }
    return on
  })
  const [previewId, setPreviewId] = useState<string | null>(null)
  const phone = usePhone()
  // The guide: its one rarely changed choice lives in Options with the others; its count is shown in the header.
  const [scheduledOnly, setScheduledOnly] = useHistoryState('guide:scheduled', true)
  const [guideTotal, setGuideTotal] = useState<number | null>(null)
  // Wide screens leave the guide room beside the header for the focused programme (see Guide).
  const wide = useWide()
  const [side, setSide] = useState<HTMLDivElement | null>(null)
  const [optionsOpen, setOptionsOpen] = useState(false)
  // Search is an icon until it is asked for, and stays a field for as long as it holds text.
  const [searchOpen, setSearchOpen] = useState(false)
  const searching = searchOpen || search !== ''
  useEffect(() => { if (searchOpen) void setFocus('live:search') }, [searchOpen])
  // The × beside the field empties and closes it; focus goes to the icon that takes the field's place.
  const cleared = useRef(false)
  const clearSearch = () => { cleared.current = true; setSearch(''); setSearchOpen(false) }
  useEffect(() => { if (!searching && cleared.current) { cleared.current = false; void setFocus('live:search-open') } }, [searching])
  const languages = useLanguages()
  const { offered: languageOffered, language } = languageScope(languages.data, lang, profile?.languages.join(',') ?? '')
  // A region row is a choice only when United States is some, not all or none, of what is listed.
  // Group counts answer that without asking the server for (and having it remember) two more channel lists.
  const anywhere = channelCount(useChannelGroups({ language, country: 'all' }, saved.ready).data)
  const domestic = channelCount(useChannelGroups({ language, country: 'US' }, saved.ready).data)
  const regionKnown = anywhere != null && domestic != null
  const regionOffered = regionKnown && domestic > 0 && domestic < anywhere
  const region = !regionKnown || regionOffered || (country === 'US' && domestic === anywhere) ? country : 'all'
  const filterKey = `${language}|${region}|${group}|${source}|${favOnly}|${term}`
  const [pageKey, setPageKey] = useState(filterKey)
  // Reset paging synchronously with filters so no request briefly targets an old offset.
  if (pageKey !== filterKey) { setPageKey(filterKey); setPage(0) }
  const filters = { language, country: region, source_id: source || undefined, favorites: favOnly || undefined, q: term || undefined }
  const q = useChannels({ ...filters, group: group || undefined, limit: PAGE, offset: page * PAGE }, saved.ready && view === 'channels')
  // The chip row stays put while a search or Favorites narrows the list below it.
  const groups = useChannelGroups({ language, country: region, source_id: source || undefined }, saved.ready)
  const sources = useSources()
  const channels = useMemo(() => q.data?.channels ?? [], [q.data])
  const total = q.data?.total ?? channels.length
  const ready = saved.ready && view === 'channels' && !!q.data && !q.isPlaceholderData
  const focused = channels.find((c) => c.id === focusedId) ?? null
  const setChannelList = useUi((s) => s.setChannelList)
  const toggleFav = useToggleFavorite()
  // Recording the focused channel's now or next, where the server has recording on and this profile may schedule.
  const recordings = useRecordings(useCan('recordings'), false)
  const recordable = !!recordings.data?.enabled && recordings.data.can_schedule !== false
  const [recordingId, setRecordingId] = useState<string | null>(null)
  const recordingChannel = channels.find((c) => c.id === recordingId)
  const go = useGo()
  useEffect(() => { if (ready && q.data) setChannelList(q.data.list_id ?? null, channels) }, [ready, q.data, channels, setChannelList])
  // Arrive on the channels, not on the search field (a focused field raises the on-screen keyboard).
  useRestoreFocus(ready, channels.length ? 'live:list' : search ? 'live:search' : 'live:group:all')
  useEffect(() => {
    if (view !== 'channels' || !previewEnabled || !focused) { setPreviewId(null); return }
    const id = window.setTimeout(() => setPreviewId(focused.id), 600)
    return () => window.clearTimeout(id)
  }, [focused, previewEnabled, view])

  // D-pad number entry searches the complete catalog, not just the current page.
  const digits = useRef('')
  useEffect(() => {
    let timer = 0
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t?.tagName === 'INPUT' || t?.tagName === 'TEXTAREA' || !/^[0-9]$/.test(e.key) || e.ctrlKey || e.metaKey || e.altKey) return
      digits.current = (digits.current + e.key).slice(-6)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        setSearch(digits.current); digits.current = ''
        // The field is drawn by this update when search was still an icon: the effect above focuses it then.
        setSearchOpen(true)
        if (doesFocusableExist('live:search')) void setFocus('live:search')
      }, 700)
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(timer) }
  }, [setSearch])
  const playChannel = (c: Channel) => {
    setChannelList(q.data?.list_id ?? null, channels)
    go(`/play/${encodeURIComponent(c.id)}${q.data?.list_id ? `?list=${encodeURIComponent(q.data.list_id)}` : ''}`)
  }
  // The empty list's way out: every filter off, and focus on All, which is now the choice.
  const reset = () => { saved.update((v) => ({ ...v, group: '', source: '', favorites: false })); setSearch(''); setSearchOpen(false); setPage(0); void setFocus('live:group:all') }
  const changePage = (p: number) => { setPage(p); setPreviewId(null); setFocusedId(null); void setFocus('live:groups') }
  const shownGroups = (groups.data ?? []).filter((g) => !g.hidden)
  // Chips are the top level. A group with children is one chip that drops them down and names the one in force.
  const topGroups = shownGroups.filter((g) => !g.parent)
  const childrenOf = (top: ChannelGroup) => shownGroups.filter((g) => g.parent === top.name && g.source_id === top.source_id)
  const chosen = shownGroups.find((g) => g.id === group)
  const chip = chosen?.parent ? topGroups.find((g) => g.name === chosen.parent && g.source_id === chosen.source_id) : chosen
  const chipGroups = topGroups.slice(0, GROUP_CHIPS)
  const chipInRow = chipGroups.some((g) => g.id === chip?.id)
  const sourceOptions = [{ id: '', label: 'All sources' }, ...(sources.data ?? []).filter((s) => s.type === 'm3u' || s.type === 'xtream').map((s) => ({ id: s.id, label: s.name }))]

  // A choice made in Options is named in the header only while it is not the usual one.
  const notes = [
    view === 'guide' && !scheduledOnly && 'All channels',
    !!source && (sources.data?.find((x) => x.id === source)?.name ?? 'One source'),
    languageOffered && lang !== defaults.language && (lang === 'mine' ? 'My languages' : lang === 'all' ? 'All languages' : LANG_NAMES[lang] ?? languages.data?.find((l) => l.code === lang)?.name ?? lang),
    regionOffered && country !== 'US' && (country === 'all' ? 'All regions' : 'Unknown region'),
  ].filter((n): n is string => !!n)
  const counted = view === 'channels' ? (q.isFetching ? 'Updating…' : `${total.toLocaleString()} channels`) : guideTotal === null ? '' : `${guideTotal.toLocaleString()} channels`

  if (!saved.ready) return <div className="live-screen" data-testid="live"><h1 className="heading">Live TV</h1>{saved.error ? <><p role="alert">{saved.error}</p><Button focusKey="live:retry-prefs" onEnter={() => void saved.retry()}>Retry</Button></> : <p role="status">Loading saved filters…</p>}</div>
  return <div className="live-screen" data-testid="live" data-view={view}>
    <div className="live-head" data-testid="live-head">
      {/* Up from the channels or the guide arrives on the groups, at the one in force; the line above them is one more Up. */}
      <FocusGroup focusKey="live:filters" className="live-head-main" preferred="live:groups">
        <div className="live-bar">
          <h1 className="heading">Live TV</h1>
          <FocusGroup focusKey="live:views" className="flex gap-2" role="group" label="Live TV view">
            <Chip focusKey="live:view:guide" pressed={view === 'guide'} onEnter={() => setView('guide')}>Guide</Chip>
            <Chip focusKey="live:view:channels" pressed={view === 'channels'} onEnter={() => setView('channels')}>Channels</Chip>
          </FocusGroup>
          <div className="live-tools" onBlur={(e) => { if (!search && !e.currentTarget.contains(e.relatedTarget)) setSearchOpen(false) }}>
            {searching
              ? <div className="live-search"><FocusableInput focusKey="live:search" label="Search channels or number" placeholder="Channel name or number" value={search} onChange={setSearch} inputMode="search" /></div>
              : <Button focusKey="live:search-open" className="btn-chip" iconOnly icon="search" label="Search channels" onEnter={() => setSearchOpen(true)} testId="live-search-open" />}
            {/* The one ×: it clears the search. A group or Favorites is left by choosing All. */}
            {search !== '' && <Chip focusKey="live:search-clear" label="Clear search" onEnter={clearSearch}><Icon name="x" size={14} /></Chip>}
            <Button focusKey="live:options" className={notes.length ? 'btn-chip live-changed' : 'btn-chip'} iconOnly icon="filter" label="Options" onEnter={() => setOptionsOpen(true)} testId="live-options" />
          </div>
          <p className="label live-note" aria-live="polite" data-testid="live-note"><span>{counted}</span>{notes.map((n) => <span key={n} data-testid="live-choice">{n}</span>)}</p>
        </div>
        <FocusGroup focusKey="live:groups" className="chip-scroll" role="group" label="Channel groups" preferred={favOnly ? 'live:fav' : `live:group:${chipInRow ? chip!.id : 'all'}`}>
          <Chip focusKey="live:group:all" pressed={!group && !favOnly} onEnter={() => pick('')}>All</Chip>
          <Chip focusKey="live:fav" pressed={favOnly} onEnter={() => pick('', !favOnly)} testId="live-fav-filter">Favorites</Chip>
          {chipGroups.map((g) => {
            const below = childrenOf(g)
            return below.length
              ? <ChipMenu key={g.id} focusKey={`live:group:${g.id}`} name={g.name} value={group} all={{ id: g.id, label: `All ${g.name}`, count: g.channel_count }}
                options={below.map((c) => ({ id: c.id, label: c.name, count: c.channel_count }))} onChange={(id) => pick(id)} />
              : <Chip key={g.id} focusKey={`live:group:${g.id}`} pressed={group === g.id} onEnter={() => pick(g.id)}>{g.name}</Chip>
          })}
          {topGroups.length > GROUP_CHIPS && <CatalogPicker scope="live:category" label="More groups" value={group} options={[{ id: '', label: 'All groups' }, ...shownGroups.map((g) => ({ id: g.id, label: g.path, count: g.channel_count }))]} onChange={(id) => pick(id)} />}
        </FocusGroup>
        {saved.error && <p className="label" role="alert">{saved.error}</p>}
        {groups.error && <p role="status" className="muted">Groups could not load. You can still search channels.</p>}
      </FocusGroup>
      {view === 'guide' && wide && <div ref={setSide} className="live-head-side" />}
    </div>
    <Modal open={optionsOpen} onClose={() => setOptionsOpen(false)} title="Options" firstFocusKey="guide:scheduled" variant="sheet" testId="live-options-sheet">
      <section className="flex flex-col gap-2"><h3 className="label">The guide lists</h3>
        <FocusGroup focusKey="live:schedules" className="flex gap-2 flex-wrap" role="group" label="Guide channels">
          <Chip focusKey="guide:scheduled" pressed={scheduledOnly} onEnter={() => setScheduledOnly(true)}>With schedules</Chip>
          <Chip focusKey="guide:all-channels" pressed={!scheduledOnly} onEnter={() => setScheduledOnly(false)}>All channels</Chip>
        </FocusGroup>
      </section>
      {/* a row with a single choice is not shown */}
      {languageOffered && <section className="flex flex-col gap-2"><h3 className="label">Language</h3><LanguageFilter scope="live" value={lang} onChange={setLang} /></section>}
      {regionOffered && <section className="flex flex-col gap-2"><h3 className="label">Region</h3>
        <FocusGroup focusKey="live:regions" className="flex gap-2 flex-wrap" role="group" label="Region">
          <Chip focusKey="live:country:US" pressed={country === 'US'} onEnter={() => setCountry('US')}>United States</Chip>
          <Chip focusKey="live:country:all" pressed={country === 'all'} onEnter={() => setCountry('all')}>All regions</Chip>
          <Chip focusKey="live:country:und" pressed={country === 'und'} onEnter={() => setCountry('und')}>Unknown region</Chip>
        </FocusGroup>
      </section>}
      {sourceOptions.length > 2 && <FocusGroup focusKey="live:sources" className="flex"><CatalogPicker scope="live:source" label="Source" value={source} options={sourceOptions} onChange={setSource} /></FocusGroup>}
      <FocusGroup focusKey="live:options-actions" className="flex gap-2 flex-wrap">
        <Button focusKey="live:options:done" primary onEnter={() => setOptionsOpen(false)}>Done</Button>
      </FocusGroup>
    </Modal>
    {view === 'guide' ? <Suspense fallback={<Skeleton className="epg-skel" />}>
      <Guide filters={{ ...filters, group: group || undefined }} scheduledOnly={scheduledOnly} onShowAll={() => setScheduledOnly(false)} selectedChannelId={focusedId} onSelectChannel={setFocusedId}
        wide={wide} detailsSlot={side} upKey="live:groups" onTotal={setGuideTotal} />
    </Suspense> : <div className="live-catalog-layout grid gap-6" data-testid="live-channels" data-preview={!phone}>
      <div>
        {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : !ready ? <div className="flex flex-col gap-2">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} style={{ height: '3.5rem' }} />)}</div> : !channels.length ?
          <Empty title="No channels match" hint={languageOffered ? 'Clear the group or search, or choose All languages to include other and unknown languages.' : 'Clear the group or search to see every channel.'}><Button focusKey="live:empty-reset" onEnter={reset}>Clear filters</Button>{languageOffered && <Button focusKey="live:empty-all" onEnter={() => { reset(); setLang('all') }}>All languages</Button>}</Empty> :
          <FocusGroup focusKey="live:list" className="flex flex-col gap-2" role="listbox" label="Channels" remember>
            {channels.map((c) => <ChannelRow key={c.id} focusKey={`live::${c.id}`} channel={c} onEnter={() => playChannel(c)} onFocus={() => setFocusedId(c.id)} />)}
          </FocusGroup>}
        <FocusGroup focusKey="live:pages" className="flex gap-3 flex-wrap items-center py-4" label="Channel pages">
          <Button focusKey="live:previous" disabled={!page || q.isFetching} onEnter={() => changePage(page - 1)}>Previous page</Button>
          <span className="label">{total ? `${(page * PAGE + 1).toLocaleString()}–${Math.min((page + 1) * PAGE, total).toLocaleString()} of ${total.toLocaleString()}` : '0 channels'}</span>
          <Button focusKey="live:next" disabled={(page + 1) * PAGE >= total || q.isFetching} onEnter={() => changePage(page + 1)}>Next page</Button>
        </FocusGroup>
      </div>
      {/* A phone has no room for a preview: selecting a channel plays it. */}
      {!phone && <aside className="live-catalog-preview flex flex-col gap-3" aria-label="Preview" style={{ position: 'sticky', top: 'calc(var(--bar-h) + 1rem)', alignSelf: 'start' }}>
        <Preview channelId={previewId} off={!previewEnabled} onTurnOn={() => setPreviewEnabled(true)} />
        {focused && <div className="flex flex-col gap-1" data-testid="live-info"><h2 className="title-xl">{focused.number} · {focused.name}</h2><p className="flex items-center gap-2 flex-wrap">{focused.now ? `Now: ${focused.now.title} (${fmtTime(focused.now.start)} to ${fmtTime(focused.now.stop)})` : 'No programme data supplied for this channel'}<RecordingBadge state={focused.now?.recording_state} /></p>{focused.next && <p className="muted flex items-center gap-2 flex-wrap">Next: {focused.next.title} at {fmtTime(focused.next.start)}<RecordingBadge state={focused.next.recording_state} /></p>}</div>}
        <FocusGroup focusKey="live:actions" className="flex gap-2 flex-wrap">
          <Button focusKey="live:watch" primary icon="play" disabled={!focused} onEnter={() => focused && playChannel(focused)}>Watch full screen</Button>
          <Chip focusKey="live:preview" pressed={previewEnabled} onEnter={() => setPreviewEnabled((v) => !v)}>Preview {previewEnabled ? 'on' : 'off'}</Chip>
          <Button focusKey="live:togglefav" icon="heart" disabled={!focused} pressed={!!focused?.is_favorite} onEnter={() => focused && toggleFav.mutate({ kind: 'channel', id: focused.id, on: !focused.is_favorite })}>{focused?.is_favorite ? 'Remove favorite' : 'Add favorite'}</Button>
          {recordable && <Button focusKey="live:record" icon="record" disabled={!focused?.now && !focused?.next} onEnter={() => focused && setRecordingId(focused.id)} testId="live-record">Record</Button>}
        </FocusGroup>
      </aside>}
    </div>}
    <RecordDialog target={recordingChannel ? { channel: recordingChannel, programmes: [recordingChannel.now, recordingChannel.next].filter((p) => !!p) } : null} onClose={() => setRecordingId(null)} />
  </div>
}
