import { useState } from 'react'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { useBrowseGenres } from '../api/browseGenres'
import { useParams, useSearchParams } from 'react-router'
import { useBrowse, useBrowseCategories, useBrowseLanguages, useBrowseShelves, useSources, type BrowseFilters } from '../api/hooks'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { CatalogPicker } from '../components/CatalogPicker'
import { Icon } from '../components/Icon'
import { LanguageFilter, languageScope } from '../components/LanguageFilter'
import { MediaTile, TileRow } from '../components/Tile'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'
import { useHistoryState } from '../nav/historyState'
import { usePhone } from '../lib/layout'
import { LANG_NAMES, useDebounced } from '../lib/util'

const TITLES: Record<string, string> = { movies: 'Movies', shows: 'Shows', vod: 'Movies on demand', series: 'Series on demand' }
const SORTS: Array<{ id: NonNullable<BrowseFilters['sort']>; label: string }> = [
  { id: 'added', label: 'Recently added' }, { id: 'title', label: 'Title' }, { id: 'year', label: 'Year' }, { id: 'rating', label: 'Rating' },
]
// Enough rows to browse by scrolling; each ends in See all, so a row stays a short walk with a remote.
const SHELVES = { limit_rows: 24, limit_items: 12 }
const LANGUAGE_LABEL: Record<string, string> = { mine: 'My languages', all: 'All languages' }
const capital = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1)

const filterSchema = z.object({
  source: z.string().max(256), sort: z.enum(['added', 'title', 'year', 'rating']),
  language: z.string().max(256), category: z.string().max(256), genre: z.string().max(256),
})
type SavedFilters = z.infer<typeof filterSchema>
const validateFilters = (raw: unknown) => { const parsed = filterSchema.safeParse(raw); return parsed.success ? parsed.data : null }

export function Browse() {
  const section = useParams().section ?? 'movies'
  const [params, setParams] = useSearchParams()
  const phone = usePhone()
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  const defaults: SavedFilters = { source: '', sort: 'added', language: preferred ? 'mine' : 'all', category: '', genre: '' }
  const saved = useProfilePreference(`browseFilters.v1.${section}`, defaults, validateFilters)
  const { source, sort, language: lang, genre } = saved.value
  // A "See all" link names its shelf in the address: it holds for that visit and is never a saved filter.
  const linked = params.get('category')
  const category = linked ?? saved.value.category
  const unlink = () => { if (linked !== null) setParams({}, { replace: true }) }
  const setSource = (source: string) => { saved.update((v) => ({ ...v, source, category: '' })); unlink() }
  const setSort = (sort: SavedFilters['sort']) => saved.update((v) => ({ ...v, sort }))
  const setLang = (language: string) => saved.update((v) => ({ ...v, language }))
  const setCategory = (category: string) => { if (linked === null) saved.update((v) => ({ ...v, category })); else setParams(category ? { category } : {}, { replace: true }) }
  const setGenre = (genre: string) => saved.update((v) => ({ ...v, genre }))
  const [search, setSearch] = useHistoryState(`browse:${section}:search`, '')
  const term = useDebounced(search, 300)
  // Holds the control the Filter panel opens on. Fixed while it is open: the panel's focus bookkeeping
  // restarts if that key changes, which a change of sort would otherwise cause.
  const [sheet, setSheet] = useState<string | null>(null)
  const sources = useSources()
  const languages = useBrowseLanguages(section, source || undefined)
  const { offered: languageOffered, language } = languageScope(languages.data, lang, preferred)
  const [page, setPage] = useHistoryState(`browse:${section}:page`, 1)
  const filterKey = `${section}|${source}|${sort}|${language}|${category}|${genre}|${term}`
  const [pageKey, setPageKey] = useState(filterKey)
  if (pageKey !== filterKey) { setPageKey(filterKey); setPage(1) }
  // Shelves are the way in. Asking for anything narrower (a filter, a search, one shelf) shows the grid.
  const filtered = !!(source || category || genre || term || sort !== defaults.sort || lang !== defaults.language)
  const shelves = useBrowseShelves(section, { language, ...SHELVES }, saved.ready && !filtered)
  const onShelves = !filtered && !shelves.error && (!shelves.data || shelves.data.shelves.length > 0)
  const filters = { source_id: source || undefined, language, q: term || undefined }
  const categories = useBrowseCategories(section, filters)
  const genres = useBrowseGenres(section, { ...filters, category: category || undefined })
  const q = useBrowse(section, { ...filters, sort, category: category || undefined, genre: genre || undefined }, page, saved.ready && !onShelves)
  const go = useGo()
  const tiles = q.data?.tiles ?? []
  const total = q.data?.total ?? tiles.length
  const pageSize = q.data?.page_size ?? 60
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const gridReady = !!q.data && !q.isPlaceholderData
  const ready = saved.ready && (onShelves ? !!shelves.data : gridReady)
  // Arrive on something to watch, not on the search field (a focused field raises the on-screen keyboard).
  useRestoreFocus(ready, onShelves ? 'browse:shelves' : tiles.length ? 'browse:grid' : 'browse:filter')
  const libs = (sources.data ?? []).filter((s) => s.type !== 'streaming_service' && s.enabled)
  const reset = () => { saved.update(defaults); setSearch(''); setPage(1); unlink(); void setFocus('browse:filter') }
  const kind = section === 'shows' || section === 'series' ? 'shows' : 'movies'

  const active: Array<{ id: string; text: string; clear: () => void }> = []
  if (term) active.push({ id: 'search', text: `Search: ${term}`, clear: () => setSearch('') })
  if (category) active.push({ id: 'category', text: `Category: ${category}`, clear: () => setCategory('') })
  if (genre) active.push({ id: 'genre', text: `Genre: ${capital(genre)}`, clear: () => setGenre('') })
  if (source) active.push({ id: 'source', text: `Source: ${libs.find((s) => s.id === source)?.name ?? 'One source'}`, clear: () => setSource('') })
  if (sort !== defaults.sort) active.push({ id: 'sort', text: `Sort: ${SORTS.find((s) => s.id === sort)?.label ?? sort}`, clear: () => setSort(defaults.sort) })
  if (lang !== defaults.language) active.push({ id: 'language', text: `Language: ${LANGUAGE_LABEL[lang] ?? LANG_NAMES[lang] ?? lang}`, clear: () => setLang(defaults.language) })
  // The chip under focus is about to go; hand focus to its neighbour, or back to Filter after the last one.
  const drop = (id: string, clear: () => void) => {
    const next = active.find((f) => f.id !== id)
    void setFocus(next ? `browse:active:${next.id}` : 'browse:filter')
    clear()
  }

  if (!saved.ready) return <div className="page-pad" data-testid="browse"><h1 className="title-xl">{TITLES[section] ?? section}</h1>{saved.error ? <><p role="alert">{saved.error}</p><Button focusKey="browse:retry-prefs" onEnter={() => void saved.retry()}>Retry</Button></> : <p role="status">Loading saved filters…</p>}</div>
  return <div data-testid="browse" data-view={onShelves ? 'shelves' : 'grid'}>
    <div className="page-pad browse-head">
      <FocusGroup focusKey="browse:filters" className="browse-bar">
        {phone ? <FocusGroup focusKey="browse:switch" className="browse-switch" role="group" label="Library">
          <h1 className="sr-only">{TITLES[section] ?? section}</h1>
          <Chip focusKey="browse:switch:movies" pressed={kind === 'movies'} onEnter={() => { if (kind !== 'movies') go('/browse/movies', { replace: true }) }}>Movies</Chip>
          <Chip focusKey="browse:switch:shows" pressed={kind === 'shows'} onEnter={() => { if (kind !== 'shows') go('/browse/shows', { replace: true }) }}>Shows</Chip>
        </FocusGroup> : <h1 className="title-xl">{TITLES[section] ?? section}</h1>}
        <div className="browse-search"><FocusableInput focusKey="browse:search" label="Search this library" placeholder={`Search ${(TITLES[kind] ?? 'titles').toLowerCase()}`} value={search} onChange={setSearch} inputMode="search" /></div>
        <Button focusKey="browse:filter" icon="filter" onEnter={() => setSheet(`browse:sort:${sort}`)} testId="browse-filter">Filter</Button>
      </FocusGroup>
      {filtered && <FocusGroup focusKey="browse:active" className="chip-scroll" role="group" label="Active filters">
        {active.map((f) => <Chip key={f.id} focusKey={`browse:active:${f.id}`} label={`Remove filter ${f.text}`} testId="active-filter" onEnter={() => drop(f.id, f.clear)}>{f.text}<Icon name="x" size={14} /></Chip>)}
        <Button focusKey="browse:clear" ghost onEnter={reset}>Clear filters</Button>
      </FocusGroup>}
      {!onShelves && <p className="label" aria-live="polite">{q.isFetching ? 'Updating…' : `${total.toLocaleString()} titles`}</p>}
      {saved.error && <p className="label" role="alert">{saved.error}</p>}
    </div>
    {onShelves ? (shelves.data ? <FocusGroup focusKey="browse:shelves" className="pb-16">
      {shelves.data.shelves.map((s) => <TileRow key={s.name} id={`shelf:${s.name}`} title={s.name} tiles={s.items} count={s.count} seeAll={s.browse_path} />)}
    </FocusGroup> : <div className="page-pad flex gap-4" style={{ paddingTop: 0 }} data-testid="browse-loading">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} style={{ flex: 'none', width: 'var(--tile-poster-w)', height: 'var(--tile-poster-h)' }} />)}</div>) : <div className="page-pad" style={{ paddingTop: 0 }}>
      {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : !gridReady ? <div className="grid-tiles">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} style={{ aspectRatio: '2/3' }} />)}</div> : !tiles.length ?
        <Empty title="No matching titles" hint="Try another genre, category or All languages. Titles without language metadata are listed as Unknown, not English."><Button focusKey="browse:reset" onEnter={reset}>Clear filters</Button><Button focusKey="browse:all" onEnter={() => { saved.update({ ...defaults, language: 'all' }); setSearch(''); setPage(1); unlink() }}>All languages</Button><Button focusKey="browse:sources" onEnter={() => go('/sources')}>Manage sources</Button></Empty> :
        <FocusGroup focusKey="browse:grid" className="grid-tiles">{tiles.map((t) => <MediaTile key={t.id} tile={t} scope="browse" />)}</FocusGroup>}
      {pages > 1 && <FocusGroup focusKey="browse:pages" className="flex gap-3 flex-wrap items-center py-4" label="Library pages">
        <Button focusKey="browse:previous" disabled={page <= 1 || q.isFetching} onEnter={() => { setPage(page - 1); window.scrollTo({ top: 0 }) }}>Previous page</Button>
        <span className="label">Page {page} of {pages.toLocaleString()}</span>
        <Button focusKey="browse:more" disabled={(q.data?.has_more === false || page >= pages) || q.isFetching} onEnter={() => { setPage(page + 1); window.scrollTo({ top: 0 }) }}>Next page</Button>
      </FocusGroup>}
    </div>}

    <Modal open={sheet !== null} onClose={() => setSheet(null)} title="Filter" firstFocusKey={sheet ?? 'browse:done'} variant="panel" testId="browse-filters">
      <section className="flex flex-col gap-2"><h3 className="label">Sort by</h3>
        <FocusGroup focusKey="browse:sorts" className="flex gap-2 flex-wrap" role="group" label="Sort titles">{SORTS.map((s) => <Chip key={s.id} focusKey={`browse:sort:${s.id}`} pressed={sort === s.id} onEnter={() => setSort(s.id)}>{s.label}</Chip>)}</FocusGroup>
      </section>
      <FocusGroup focusKey="browse:pickers" className="flex flex-col gap-2 items-start">
        <CatalogPicker scope="browse:category" label="Category" value={category} selectedLabel={category || undefined} options={[{ id: '', label: 'All categories' }, ...(categories.data ?? []).map((c) => ({ id: c.name, label: c.name, count: c.count }))]} onChange={setCategory} />
        <CatalogPicker scope="browse:genre" label="Genre" value={genre} selectedLabel={genre ? capital(genre) : undefined} options={[{ id: '', label: 'All genres' }, ...(genres.data ?? []).map((g) => ({ id: g.name, label: capital(g.name), count: g.count }))]} onChange={setGenre} />
        {/* one source is not a choice */}
        {libs.length > 1 && <CatalogPicker scope="browse:source" label="Source" value={source} options={[{ id: '', label: 'All sources' }, ...libs.map((s) => ({ id: s.id, label: s.name }))]} onChange={setSource} />}
      </FocusGroup>
      {genres.error && <p className="label" role="alert">Genres could not be loaded. Try refreshing the page.</p>}
      {languageOffered && <section className="flex flex-col gap-2"><h3 className="label">Language</h3>
        <LanguageFilter scope="browse" value={lang} onChange={setLang} languages={languages.data} />
      </section>}
      <p className="label" role="status">{saved.error ?? (saved.saving ? 'Saving filters…' : 'Filters are saved for this profile.')}</p>
      <FocusGroup focusKey="browse:sheet-actions" className="flex gap-2 flex-wrap">
        <Button focusKey="browse:done" primary onEnter={() => setSheet(null)}>Done</Button>
        {filtered && <Button focusKey="browse:sheet-clear" ghost onEnter={() => { setSheet(null); reset() }}>Clear filters</Button>}
      </FocusGroup>
    </Modal>
  </div>
}
