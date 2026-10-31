import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router'
import { useBrowse, useSources, type BrowseFilters } from '../api/hooks'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { MediaTile } from '../components/Tile'
import { FocusGroup } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'

const TITLES: Record<string, string> = { movies: 'Movies', shows: 'Shows', vod: 'Movies on demand', series: 'Series on demand' }
const SORTS: Array<{ id: NonNullable<BrowseFilters['sort']>; label: string }> = [
  { id: 'added', label: 'Recently added' }, { id: 'title', label: 'Title' }, { id: 'year', label: 'Year' }, { id: 'rating', label: 'Rating' },
]

export function Browse() {
  const section = useParams().section ?? 'movies'
  const [source, setSource] = useState<string | undefined>()
  const [sort, setSort] = useState<NonNullable<BrowseFilters['sort']>>('added')
  const sources = useSources()
  const q = useBrowse(section, { source_id: source, sort })
  const go = useGo()
  const tiles = q.data?.pages.flatMap((p) => p.tiles ?? []) ?? []
  useRestoreFocus(tiles.length > 0, 'browse:sort:added')

  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !q.hasNextPage) return
    const io = new IntersectionObserver((e) => { if (e[0]?.isIntersecting && !q.isFetchingNextPage) void q.fetchNextPage() }, { rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
  }, [q])

  const libs = (sources.data ?? []).filter((s) => s.type !== 'streaming_service')
  return (
    <div className="page-pad" data-testid="browse">
      <h1 className="display mb-4">{TITLES[section] ?? section}</h1>
      <FocusGroup focusKey="browse:filters" className="flex gap-2 flex-wrap items-center mb-2">
        {SORTS.map((s) => <Chip key={s.id} focusKey={`browse:sort:${s.id}`} pressed={sort === s.id} onEnter={() => setSort(s.id)}>{s.label}</Chip>)}
        {libs.length > 1 && <span className="label mx-1">Source</span>}
        {libs.length > 1 && <Chip focusKey="browse:src:all" pressed={!source} onEnter={() => setSource(undefined)}>All</Chip>}
        {libs.length > 1 && libs.map((s) => <Chip key={s.id} focusKey={`browse:src:${s.id}`} pressed={source === s.id} onEnter={() => setSource(s.id)}>{s.name}</Chip>)}
      </FocusGroup>
      {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : q.isLoading ? (
        <div className="grid-tiles">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} style={{ aspectRatio: '2/3' }} />)}</div>
      ) : tiles.length === 0 ? (
        <Empty title="Nothing here yet" hint="This section fills in after a source with matching titles finishes syncing."><Button focusKey="browse:add" icon="plus" onEnter={() => go('/sources')}>Add a source</Button></Empty>
      ) : (
        <FocusGroup focusKey="browse:grid" className="grid-tiles">
          {tiles.map((t) => <MediaTile key={t.id} tile={t} scope="browse" />)}
        </FocusGroup>
      )}
      <div ref={sentinel} aria-hidden="true" style={{ height: 1 }} />
      {q.hasNextPage && <div className="flex justify-center py-4"><Button focusKey="browse:more" onEnter={() => void q.fetchNextPage()}>{q.isFetchingNextPage ? 'Loading…' : 'Load more'}</Button></div>}
    </div>
  )
}
