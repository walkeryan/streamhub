import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router'
import { api, ApiError, unwrap } from '../api/client'
import type { paths } from '../api/types'
import { MediaTile } from '../components/Tile'
import { Attribution } from '../components/ui'
import { Button, Chip, Empty, ErrorState, PageHeader, SkeletonRow } from '../ds'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'

type Page = paths['/discover/{section}']['get']['responses']['200']['content']['application/json']

export function SearchCollection({ kind }: { kind: 'actor' | 'service' }) {
  return kind === 'actor' ? <ActorFilmography /> : <Collection kind="service" />
}

function Collection({ kind }: { kind: 'actor' | 'service' }) {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const section = params.get('section') === 'shows' ? 'shows' : 'movies'
  const page = Math.max(1, Math.min(500, Number.parseInt(params.get('page') ?? '1', 10) || 1))
  const profile = useSession(s => s.profile)
  const query = useQuery({
    queryKey: ['search-collection', profile?.id, profile?.content_restricted, kind, id, section, page],
    queryFn: ({ signal }) => unwrap<Page>(api.GET('/discover/{section}', { signal, params: {
      path: { section }, query: { page, ...(kind === 'actor' ? { actor: Number(id) } : { service: id }) },
    } })),
    // Turning the page keeps the page that is showing, and the pager under it, until the next one arrives.
    placeholderData: keepPreviousData,
  })
  useRestoreFocus(true, 'collection:tabs')
  const change = (next: string, n: number) => {
    const updated = new URLSearchParams(params)
    updated.set('section', next)
    updated.set('page', String(n))
    setParams(updated, { replace: true })
  }
  return <div className="page-pad search-sections">
    <PageHeader title={params.get('name') || (kind === 'actor' ? 'Actor' : 'Streaming service')} />
    <FocusGroup focusKey="collection:tabs" className="lib-chips">
      {(['movies', 'shows'] as const).map(s => <Chip key={s} focusKey={`collection:${s}`} pressed={section === s}
        onEnter={() => change(s, 1)}>{s === 'movies' ? 'Movies' : 'TV series'}</Chip>)}
    </FocusGroup>
    {query.error ? <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      : !query.data ? <SkeletonRow layout="poster" count={6} />
      : query.data.tiles.length ? <FocusGroup focusKey="collection:titles" className="grid-tiles" data={{ stale: query.isPlaceholderData || undefined }}>
        {query.data.tiles.map(t => <MediaTile key={t.id} tile={t} scope="collection" titleAlways mark="availability" />)}
      </FocusGroup> : <Empty icon="film" title="No titles on this page" />}
    {query.data && (page > 1 || query.data.has_more) && <FocusGroup focusKey="collection:pages" className="lib-chips">
      <Button focusKey="collection:prev" disabled={page === 1 || query.isPlaceholderData} onEnter={() => change(section, page - 1)}>Previous</Button>
      <span className="label">Page {page}</span>
      <Button focusKey="collection:next" disabled={!query.data.has_more || query.isPlaceholderData} onEnter={() => change(section, page + 1)}>Next</Button>
    </FocusGroup>}
    {query.data && <Attribution text="Availability data by JustWatch" />}
  </div>
}

type Filmography = paths['/people/{person_id}/credits']['get']['responses'][200]['content']['application/json']
function ActorFilmography() {
  const { id = '' } = useParams()
  const go = useGo()
  const [params, setParams] = useSearchParams()
  const profile = useSession(s => s.profile)
  const section = params.get('section')
  const kind = section === 'movies' ? 'movie' : section === 'shows' ? 'tv' : 'all'
  const page = Math.max(1, Math.min(500, Number.parseInt(params.get('page') ?? '1', 10) || 1))
  const query = useQuery({
    queryKey: ['actor-credits', profile?.id, profile?.content_restricted, id, kind, page],
    queryFn: ({ signal }) => unwrap<Filmography>(api.GET('/people/{person_id}/credits', { signal, params: {
      path: { person_id: Number(id) }, query: { kind, page },
    } })),
    retry: false,
    placeholderData: keepPreviousData,
  })
  useRestoreFocus(true, 'credits:all')
  if (query.error instanceof ApiError && query.error.status === 404) return <Collection kind="actor" />
  const data = query.data
  const change = (section: string, nextPage: number) => {
    const updated = new URLSearchParams(params)
    updated.set('section', section); updated.set('page', String(nextPage))
    setParams(updated, { replace: true })
  }
  const credit = (row: Filmography['credits'][number], group: string) =>
    <Focusable key={row.id} role="link" className="actor-credit fx-flat" focusKey={`credits:${group}:${row.id}`}
      onEnter={() => go('/title/' + encodeURIComponent(row.id))}>
      {row.poster && <img loading="lazy" src={row.poster} alt="" />}
      <span><strong>{row.title}</strong><small>{[row.year, row.kind === 'tv' ? 'TV series' : 'Movie', ...row.roles].filter(Boolean).join(' · ')}</small></span>
    </Focusable>
  return <div className="page-pad search-sections" data-testid="actor-filmography">
    <div className="actor-head">{data?.photo && <img src={data.photo} alt="" />}<PageHeader title={data?.name || params.get('name') || 'Actor'} /></div>
    {!!data?.known_for.length && <section><h2 className="heading">Known for</h2><FocusGroup focusKey="credits:known" className="credit-grid">{data.known_for.map(item => credit(item, 'known'))}</FocusGroup></section>}
    <section><h2 className="heading">Filmography</h2>
      <FocusGroup focusKey="credits:filters" className="lib-chips">{([['all', 'All'], ['movies', 'Movies'], ['shows', 'TV series']] as const).map(([value, label]) =>
        <Chip key={value} focusKey={'credits:' + value} pressed={(section || 'all') === value} onEnter={() => change(value, 1)}>{label}</Chip>)}</FocusGroup>
      {query.error ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : !data ? <SkeletonRow layout="poster" count={4} />
        : data.credits.length ? <FocusGroup className="credit-grid" focusKey="credits:list" data={{ stale: query.isPlaceholderData || undefined }}>{data.credits.map(item => credit(item, 'all'))}</FocusGroup>
        : <Empty icon="film" title="No acting credits available" />}
      {data && (page > 1 || data.has_more) && <div className="lib-chips">
        <Button focusKey="credits:prev" disabled={page === 1 || query.isPlaceholderData} onEnter={() => change(section || 'all', page - 1)}>Previous</Button>
        <span>Page {page}</span><Button focusKey="credits:next" disabled={!data.has_more || query.isPlaceholderData} onEnter={() => change(section || 'all', page + 1)}>Next</Button>
      </div>}
    </section>
  </div>
}
