import { useState } from 'react'
import { useParams } from 'react-router'
import { useBrowse, useBrowseCategories, useBrowseLanguages, useSources, type BrowseFilters } from '../api/hooks'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { CatalogPicker } from '../components/CatalogPicker'
import { LanguageFilter } from '../components/LanguageFilter'
import { MediaTile } from '../components/Tile'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'
import { useSession } from '../store/session'
import { useHistoryState } from '../nav/historyState'
import { useDebounced } from '../lib/util'

const TITLES: Record<string, string> = { movies: 'Movies', shows: 'Shows', vod: 'Movies on demand', series: 'Series on demand' }
const SORTS: Array<{ id: NonNullable<BrowseFilters['sort']>; label: string }> = [
  { id: 'added', label: 'Recently added' }, { id: 'title', label: 'Title' }, { id: 'year', label: 'Year' }, { id: 'rating', label: 'Rating' },
]

export function Browse() {
  const section = useParams().section ?? 'movies'
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  const [source, setSource] = useHistoryState(`browse:${section}:source`, '')
  const [sort, setSort] = useHistoryState<NonNullable<BrowseFilters['sort']>>(`browse:${section}:sort`, 'added')
  const [lang, setLang] = useHistoryState(`browse:${section}:lang`, preferred ? 'mine' : 'all')
  const [category, setCategory] = useHistoryState(`browse:${section}:category`, '')
  const [search, setSearch] = useHistoryState(`browse:${section}:search`, '')
  const term = useDebounced(search, 300)
  const language = lang === 'mine' ? preferred || 'all' : lang
  const [page, setPage] = useHistoryState(`browse:${section}:page`, 1)
  const filterKey = `${section}|${source}|${sort}|${language}|${category}|${term}`
  const [pageKey, setPageKey] = useState(filterKey)
  if (pageKey !== filterKey) { setPageKey(filterKey); setPage(1) }
  const sources = useSources()
  const filters = { source_id: source || undefined, language, q: term || undefined }
  const categories = useBrowseCategories(section, filters)
  const languages = useBrowseLanguages(section, source || undefined)
  const q = useBrowse(section, { ...filters, sort, category: category || undefined }, page)
  const go = useGo()
  const tiles = q.data?.tiles ?? []
  const total = q.data?.total ?? tiles.length
  const pageSize = q.data?.page_size ?? 60
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const ready = !!q.data && !q.isPlaceholderData
  useRestoreFocus(ready, 'browse:search')
  const libs = (sources.data ?? []).filter((s) => s.type !== 'streaming_service' && s.enabled)
  const reset = () => { setSource(''); setCategory(''); setSearch(''); setPage(1) }
  return <div className="page-pad" data-testid="browse">
    <div className="flex gap-3 items-baseline flex-wrap mb-3"><h1 className="display">{TITLES[section] ?? section}</h1><span className="label" aria-live="polite">{q.isFetching ? 'Updating…' : `${total.toLocaleString()} titles`}</span></div>
    <FocusGroup focusKey="browse:filters" className="flex flex-col gap-3 mb-4">
      <LanguageFilter scope="browse" value={lang} onChange={(v) => { setLang(v); setCategory('') }} languages={languages.data} />
      <div className="flex gap-2 flex-wrap items-center">
        <div style={{ width: 'min(24rem, 100%)' }}><FocusableInput focusKey="browse:search" label="Search this library" placeholder="Search titles in this library" value={search} onChange={setSearch} inputMode="search" /></div>
        <CatalogPicker scope="browse:category" label="Category" value={category} options={[{ id: '', label: 'All categories' }, ...(categories.data ?? []).map((c) => ({ id: c.name, label: c.name, count: c.count }))]} onChange={setCategory} />
        {libs.length > 1 && <CatalogPicker scope="browse:source" label="Source" value={source} options={[{ id: '', label: 'All sources' }, ...libs.map((s) => ({ id: s.id, label: s.name }))]} onChange={(v) => { setSource(v); setCategory('') }} />}
        {(source || category || search) && <Button focusKey="browse:clear" ghost onEnter={reset}>Clear filters</Button>}
      </div>
      <div className="flex gap-2 flex-wrap" role="group" aria-label="Sort titles">{SORTS.map((s) => <Chip key={s.id} focusKey={`browse:sort:${s.id}`} pressed={sort === s.id} onEnter={() => setSort(s.id)}>{s.label}</Chip>)}</div>
    </FocusGroup>
    {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : !ready ? <div className="grid-tiles">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} style={{ aspectRatio: '2/3' }} />)}</div> : !tiles.length ?
      <Empty title="No matching titles" hint="Try another category or All languages. Titles without language metadata are listed as Unknown, not English."><Button focusKey="browse:reset" onEnter={reset}>Clear filters</Button><Button focusKey="browse:all" onEnter={() => { reset(); setLang('all') }}>All languages</Button><Button focusKey="browse:sources" onEnter={() => go('/sources')}>Manage sources</Button></Empty> :
      <FocusGroup focusKey="browse:grid" className="grid-tiles">{tiles.map((t) => <MediaTile key={t.id} tile={t} scope="browse" />)}</FocusGroup>}
    <FocusGroup focusKey="browse:pages" className="flex gap-3 flex-wrap items-center py-4" label="Library pages">
      <Button focusKey="browse:previous" disabled={page <= 1 || q.isFetching} onEnter={() => { setPage(page - 1); window.scrollTo({ top: 0 }) }}>Previous page</Button>
      <span className="label">Page {page} of {pages.toLocaleString()}</span>
      <Button focusKey="browse:more" disabled={(q.data?.has_more === false || page >= pages) || q.isFetching} onEnter={() => { setPage(page + 1); window.scrollTo({ top: 0 }) }}>Next page</Button>
    </FocusGroup>
  </div>
}
