import { useCallback, useState } from 'react'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { useBrowseGenres } from '../api/browseGenres'
import { useParams, useSearchParams } from 'react-router'
import { useBrowseCategories, useBrowseLanguages, useBrowseShelves, useSources, type BrowseFilters } from '../api/hooks'
import { Button, Chip, Empty, GlassIconButton, GlassPanel, Icon, Modal, PageHeader, SkeletonRow } from '../ds'
import { BrowseGrid } from '../components/BrowseGrid'
import { CatalogPicker } from '../components/CatalogPicker'
import { useAdultUnlock } from '../components/AdultUnlock'
import { LanguageFilter, languageScope } from '../components/LanguageFilter'
import { TileRow } from '../components/Tile'
import { FocusGroup } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { useSession } from '../store/session'
import { usePhone } from '../lib/layout'
import { LANG_NAMES } from '../lib/util'

const TITLES: Record<string, string> = { movies: 'Movies', shows: 'Shows', vod: 'Movies on demand', series: 'Series on demand' }
const SORTS: Array<{ id: NonNullable<BrowseFilters['sort']>; label: string }> = [
  { id: 'added', label: 'Recently added' }, { id: 'title', label: 'Title' }, { id: 'release', label: 'Release date' }, { id: 'rating', label: 'Rating' },
]
type Sort = NonNullable<BrowseFilters['sort']>
type Order = NonNullable<BrowseFilters['order']>
// Each sort's natural direction (the server's default) and how either direction reads.
const NATURAL: Record<Sort, Order> = { added: 'desc', title: 'asc', release: 'desc', year: 'desc', rating: 'desc' }
const DIRECTION: Record<Sort, Record<Order, string>> = {
  added: { desc: 'Newest first', asc: 'Oldest first' },
  title: { asc: 'A to Z', desc: 'Z to A' },
  release: { desc: 'Newest first', asc: 'Oldest first' },
  year: { desc: 'Newest first', asc: 'Oldest first' },
  rating: { desc: 'Highest first', asc: 'Lowest first' },
}
// Enough rows to browse by scrolling; each ends in See all, so a row stays a short walk with a remote.
const SHELVES = { limit_rows: 24, limit_items: 12 }
const LANGUAGE_LABEL: Record<string, string> = { mine: 'My languages', all: 'All languages' }
const capital = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1)

const filterSchema = z.object({
  source: z.string().max(256), sort: z.enum(['added', 'title', 'year', 'release', 'rating']),
  // '' is the sort's natural direction; filters saved before there was a choice have none.
  order: z.enum(['', 'asc', 'desc']).catch(''),
  language: z.string().max(256), category: z.string().max(256), genre: z.string().max(256),
})
type SavedFilters = z.infer<typeof filterSchema>
const validateFilters = (raw: unknown) => { const parsed = filterSchema.safeParse(raw); return parsed.success ? { ...parsed.data, sort: parsed.data.sort === 'year' ? 'release' as const : parsed.data.sort } : null }

export function Browse() {
  const section = useParams().section ?? 'movies'
  const [params, setParams] = useSearchParams()
  const phone = usePhone()
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  const defaults: SavedFilters = { source: '', sort: 'added', order: '', language: preferred ? 'mine' : 'all', category: '', genre: '' }
  const saved = useProfilePreference(`browseFilters.v1.${section}`, defaults, validateFilters)
  const { source, sort, order: savedOrder, language: lang, genre } = saved.value
  const order: Order = savedOrder || NATURAL[sort]
  // A "See all" link names its shelf in the address: it holds for that visit and is never a saved filter.
  const linked = params.get('category')
  const category = linked ?? saved.value.category
  const unlink = () => { if (linked !== null) setParams({}, { replace: true }) }
  const setSource = (source: string) => { saved.update((v) => ({ ...v, source, category: '' })); unlink() }
  // A new sort starts in its own natural direction.
  const setSort = (sort: SavedFilters['sort']) => saved.update((v) => ({ ...v, sort, order: sort === defaults.sort ? NATURAL[sort] : '' }))
  const setOrder = (next: Order) => saved.update((v) => ({ ...v, order: v.sort !== defaults.sort && next === NATURAL[v.sort] ? '' : next }))
  const setLang = (language: string) => saved.update((v) => ({ ...v, language }))
  const setCategory = (category: string) => { if (linked === null) saved.update((v) => ({ ...v, category })); else setParams(category ? { category } : {}, { replace: true }) }
  const setGenre = (genre: string) => saved.update((v) => ({ ...v, genre }))
  // Holds the control the Filter panel opens on. Fixed while it is open: the panel's focus bookkeeping
  // restarts if that key changes, which a change of sort would otherwise cause.
  const [sheet, setSheet] = useState<string | null>(null)
  const sources = useSources()
  // A shelf and its full listing share the same server-checked session scope.
  const adult = useAdultUnlock()
  const scope = { source_id: source || undefined, ...(adult.unlocked ? { include_adult: true } : {}) }
  const languages = useBrowseLanguages(section, scope)
  const { offered: languageOffered, language } = languageScope(languages.data, lang, preferred)
  const filterKey = `${section}|${source}|${sort}|${order}|${language}|${category}|${genre}|${adult.unlocked}`
  // Shelves are the way in. Asking for anything narrower (a filter, one shelf) shows the grid. Finding a
  // title by name is the search box's job, in the top bar.
  const filtered = !!(source || category || genre || sort !== defaults.sort || savedOrder || lang !== defaults.language)
  const shelves = useBrowseShelves(section, { ...scope, language, ...SHELVES }, saved.ready && !filtered)
  const onShelves = !filtered && !shelves.error && (!shelves.data || shelves.data.shelves.length > 0)
  const filters = { ...scope, language }
  const categories = useBrowseCategories(section, filters)
  const genres = useBrowseGenres(section, { ...filters, category: category || undefined })
  const gridFilters: BrowseFilters = { ...filters, sort, order: savedOrder || undefined, category: category || undefined, genre: genre || undefined }
  const go = useGo()
  const [count, setCount] = useState<{ total: number | null; fetching: boolean }>({ total: null, fetching: true })
  const onTotal = useCallback((total: number | null, fetching: boolean) => setCount({ total, fetching }), [])
  const gridReady = count.total !== null
  const ready = saved.ready && (onShelves ? !!shelves.data : gridReady)
  // Arrive on something to watch.
  useRestoreFocus(ready, onShelves ? 'browse:shelves' : count.total ? 'browse:grid' : 'browse:filter')
  const libs = (sources.data ?? []).filter((s) => s.type !== 'streaming_service' && s.enabled)
  const reset = () => { saved.update(defaults); unlink(); void setFocus('browse:filter') }
  const kind = section === 'shows' || section === 'series' ? 'shows' : 'movies'

  const active: Array<{ id: string; text: string; clear: () => void }> = []
  if (category) active.push({ id: 'category', text: `Category: ${category}`, clear: () => setCategory('') })
  if (genre) active.push({ id: 'genre', text: `Genre: ${capital(genre)}`, clear: () => setGenre('') })
  if (source) active.push({ id: 'source', text: `Source: ${libs.find((s) => s.id === source)?.name ?? 'One source'}`, clear: () => setSource('') })
  if (sort !== defaults.sort || savedOrder) active.push({ id: 'sort', text: `Sort: ${SORTS.find((s) => s.id === sort)?.label ?? sort}${savedOrder && savedOrder !== NATURAL[sort] ? `, ${DIRECTION[sort][order].toLowerCase()}` : ''}`, clear: () => saved.update(v => ({ ...v, sort: defaults.sort, order: '' })) })
  if (lang !== defaults.language) active.push({ id: 'language', text: `Language: ${LANGUAGE_LABEL[lang] ?? LANG_NAMES[lang] ?? lang}`, clear: () => setLang(defaults.language) })
  // The chip under focus is about to go; hand focus to its neighbour, or back to Filter after the last one.
  const drop = (id: string, clear: () => void) => {
    const next = active.find((f) => f.id !== id)
    void setFocus(next ? `browse:active:${next.id}` : 'browse:filter')
    clear()
  }

  const heading = TITLES[section] ?? section
  const countText = count.fetching && count.total === null ? 'Updating…' : `${(count.total ?? 0).toLocaleString()} titles`
  if (!saved.ready) return <div data-testid="browse"><div className="page-pad browse-head">
    <PageHeader title={heading} className="lib-head" />
    {saved.error ? <div className="ds-empty" role="alert">
      <span className="ds-empty-icon ds-empty-icon-alert" aria-hidden="true"><Icon name="alert" /></span>
      <p className="muted">{saved.error}</p>
      <FocusGroup className="ds-empty-actions"><Button focusKey="browse:retry-prefs" primary icon="refresh" onEnter={() => void saved.retry()}>Retry</Button></FocusGroup>
    </div>
      : <p className="sr-only" role="status">Loading saved filters…</p>}
  </div>{!saved.error && <div className="browse-shelves"><SkeletonRow layout="poster" count={8} /></div>}</div>
  return <div data-testid="browse" data-view={onShelves ? 'shelves' : 'grid'}>
    <div className="page-pad browse-head">
      {/* On a phone the Movies/Shows switch takes the title's place (the name stays for assistive tech). */}
      <PageHeader title={heading} meta={!onShelves && !phone ? <span aria-hidden="true">{countText}</span> : undefined} className="lib-head browse-title">
        <FocusGroup focusKey="browse:filters" className="browse-bar">
          {phone && <FocusGroup focusKey="browse:switch" className="browse-switch" role="group" label="Library">
            <Chip focusKey="browse:switch:movies" pressed={kind === 'movies'} onEnter={() => { if (kind !== 'movies') go('/browse/movies', { replace: true }) }}>Movies</Chip>
            <Chip focusKey="browse:switch:shows" pressed={kind === 'shows'} onEnter={() => { if (kind !== 'shows') go('/browse/shows', { replace: true }) }}>Shows</Chip>
          </FocusGroup>}
          {/* The order is a state as much as an action: it keeps its words ("A to Z"). */}
          {!onShelves && <Button focusKey="browse:order" className="btn-chip" icon={order === 'asc' ? 'up' : 'down'} onEnter={() => setOrder(order === 'asc' ? 'desc' : 'asc')} testId="browse-order" label={`Sorted ${DIRECTION[sort][order]}. Reverse the order`}>{DIRECTION[sort][order]}</Button>}
          <GlassIconButton focusKey="browse:filter" icon="filter" label="Filter" onEnter={() => setSheet(`browse:sort:${sort}`)} testId="browse-filter" expanded={sheet !== null} popup="dialog" />
        </FocusGroup>
      </PageHeader>
      {filtered && <FocusGroup focusKey="browse:active" className="chip-scroll" role="group" label="Active filters">
        {active.map((f) => <Chip key={f.id} focusKey={`browse:active:${f.id}`} label={`Remove filter ${f.text}`} testId="active-filter" onEnter={() => drop(f.id, f.clear)}>{f.text}<Icon name="x" size={14} /></Chip>)}
        <Button focusKey="browse:clear" ghost className="btn-chip" onEnter={reset}>Clear filters</Button>
      </FocusGroup>}
      {adult.unlocked && <GlassPanel className="lib-note" testId="adult-unlocked">
        <Icon name="eye" size={18} />
        <span className="label">Adult titles are shown on this device.</span>
        <FocusGroup focusKey="browse:adult" className="lib-note-action">
          <GlassIconButton focusKey="browse:adult-lock" size="sm" icon="lock" label="Lock adult titles" tip="Lock" onEnter={adult.lock} testId="adult-lock" />
        </FocusGroup>
      </GlassPanel>}
      {/* The count is in the title line on wide screens; this line says it to assistive tech, and shows it on a phone. */}
      {!onShelves && <p className={phone ? 'label' : 'sr-only'} aria-live="polite">{countText}</p>}
      {saved.error && <p className="label" role="alert">{saved.error}</p>}
    </div>
    {onShelves ? (shelves.data ? <FocusGroup focusKey="browse:shelves" className="browse-shelves">
      {shelves.data.shelves.map((s) => <TileRow key={s.name} id={`shelf:${s.name}`} title={s.name} tiles={s.items} count={s.count} seeAll={s.browse_path} />)}
    </FocusGroup> : <div className="browse-shelves" data-testid="browse-loading"><SkeletonRow layout="poster" count={8} /><SkeletonRow layout="poster" count={8} /></div>) : <div className="page-pad browse-body">
      <BrowseGrid section={section} filters={gridFilters} filterKey={filterKey} enabled={saved.ready && !onShelves} onTotal={onTotal} empty={
        <Empty icon="filter" title="No matching titles" hint="Try another genre, category or All languages. Titles without language metadata are listed as Unknown, not English."><Button focusKey="browse:reset" primary onEnter={reset}>Clear filters</Button><Button focusKey="browse:all" onEnter={() => { saved.update({ ...defaults, language: 'all' }); unlink() }}>All languages</Button><Button focusKey="browse:sources" ghost icon="source" onEnter={() => go('/sources')}>Manage sources</Button></Empty>} />
    </div>}

    <Modal open={sheet !== null} onClose={() => setSheet(null)} title="Filter" firstFocusKey={sheet ?? 'browse:done'} variant="panel" testId="browse-filters">
      <section className="lib-filter-group"><h3 className="lib-filter-head">Sort by</h3>
        <FocusGroup focusKey="browse:sorts" className="lib-chips" role="group" label="Sort titles">{SORTS.map((s) => <Chip key={s.id} focusKey={`browse:sort:${s.id}`} pressed={sort === s.id} onEnter={() => setSort(s.id)}>{s.label}</Chip>)}</FocusGroup>
        <FocusGroup focusKey="browse:orders" className="lib-chips" role="group" label="Sort direction">{(['asc', 'desc'] as const).map((o) => <Chip key={o} focusKey={`browse:order:${o}`} pressed={order === o} testId={`browse-order-${o}`} onEnter={() => setOrder(o)}>{DIRECTION[sort][o]}</Chip>)}</FocusGroup>
      </section>
      <section className="lib-filter-group"><h3 className="lib-filter-head">Narrow down</h3>
        <FocusGroup focusKey="browse:pickers" className="lib-chips">
          <CatalogPicker scope="browse:category" label="Category" value={category} selectedLabel={category || undefined} options={[{ id: '', label: 'All categories' }, ...(categories.data ?? []).map((c) => ({ id: c.name, label: c.name, count: c.count }))]} onChange={setCategory} />
          <CatalogPicker scope="browse:genre" label="Genre" value={genre} selectedLabel={genre ? capital(genre) : undefined} options={[{ id: '', label: 'All genres' }, ...(genres.data ?? []).map((g) => ({ id: g.name, label: capital(g.name), count: g.count }))]} onChange={setGenre} />
          {/* one source is not a choice */}
          {libs.length > 1 && <CatalogPicker scope="browse:source" label="Source" value={source} options={[{ id: '', label: 'All sources' }, ...libs.map((s) => ({ id: s.id, label: s.name }))]} onChange={setSource} />}
        </FocusGroup>
        {genres.error && <p className="label" role="alert">Genres could not be loaded. Try refreshing the page.</p>}
      </section>
      {languageOffered && <section className="lib-filter-group"><h3 className="lib-filter-head">Language</h3>
        <LanguageFilter scope="browse" value={lang} onChange={setLang} languages={languages.data} />
      </section>}
      {/* only ever rendered for a holder of watch.adult */}
      {adult.offered && <section className="lib-filter-group" data-testid="adult-titles"><h3 className="lib-filter-head">Adult titles</h3>
        {adult.unlocked
          ? <><p className="label">Shown on this device until you lock them, four hours at most.</p><div><Button focusKey="browse:adult-relock" icon="lock" onEnter={adult.lock}>Lock adult titles</Button></div></>
          : <><p className="label">Hidden. Your PIN shows them on this device for four hours.</p><div><Button focusKey="browse:adult-unlock" icon="lock" onEnter={() => { setSheet(null); adult.ask() }} testId="adult-unlock">Unlock adult titles</Button></div></>}
      </section>}
      <p className="label lib-filter-status" role="status">{saved.error ?? (saved.saving ? 'Saving filters…' : 'Filters are saved for this profile.')}</p>
      <FocusGroup focusKey="browse:sheet-actions" className="lib-actions">
        <Button focusKey="browse:done" primary onEnter={() => setSheet(null)}>Done</Button>
        {filtered && <Button focusKey="browse:sheet-clear" ghost onEnter={() => { setSheet(null); reset() }}>Clear filters</Button>}
      </FocusGroup>
    </Modal>
    {adult.dialog}
  </div>
}
