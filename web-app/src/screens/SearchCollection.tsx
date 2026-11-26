import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router'
import { api, unwrap } from '../api/client'
import type { paths } from '../api/types'
import { MediaTile } from '../components/Tile'
import { Attribution } from '../components/ui'
import { Button, Chip, Empty, ErrorState, PageHeader, SkeletonRow } from '../ds'
import { FocusGroup } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'

type Page = paths['/discover/{section}']['get']['responses']['200']['content']['application/json']

export function SearchCollection({ kind }: { kind: 'actor' | 'service' }) {
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
      : query.data.tiles.length ? <FocusGroup focusKey="collection:titles" className="grid-tiles">
        {query.data.tiles.map(t => <MediaTile key={t.id} tile={t} scope="collection" titleAlways mark="availability" />)}
      </FocusGroup> : <Empty icon="film" title="No titles on this page" />}
    {query.data && (page > 1 || query.data.has_more) && <FocusGroup focusKey="collection:pages" className="lib-chips">
      <Button focusKey="collection:prev" disabled={page === 1} onEnter={() => change(section, page - 1)}>Previous</Button>
      <span className="label">Page {page}</span>
      <Button focusKey="collection:next" disabled={!query.data.has_more} onEnter={() => change(section, page + 1)}>Next</Button>
    </FocusGroup>}
    {query.data && <Attribution text="Availability data by JustWatch" />}
  </div>
}
