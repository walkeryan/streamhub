import { useState } from 'react'
import { useFavorites } from '../api/hooks'
import { Button, Empty, ErrorState, Skeleton } from '../components/ui'
import { ChannelTile, MediaTile } from '../components/Tile'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'

export function Favorites() {
  const { data, error, isLoading, refetch } = useFavorites()
  const go = useGo()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const filtered = (data ?? []).filter((t) => t.title.toLowerCase().includes(search.trim().toLowerCase()))
  const pageIndex = Math.min(page, Math.max(0, Math.ceil(filtered.length / 80) - 1))
  const visible = filtered.slice(pageIndex * 80, (pageIndex + 1) * 80)
  useRestoreFocus(!!data, data?.length ? 'MAIN' : 'fav:browse')
  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  const chans = visible.filter((t) => t.kind === 'channel')
  const rest = visible.filter((t) => t.kind !== 'channel')
  return (
    <div className="page-pad" data-testid="favorites">
      <h1 className="display mb-4">Favorites</h1>
      {!!data?.length && <div className="mb-4" style={{ maxWidth: '26rem' }}><FocusableInput focusKey="fav:search" label="Search favorites" placeholder="Search your favorites" value={search} onChange={(v) => { setSearch(v); setPage(0) }} /></div>}
      {isLoading ? <Skeleton style={{ height: '10rem' }} /> : !data?.length ? (
        <Empty title="No favorites yet" hint="Open any title or channel and choose Favorite. It will show up here and on Home.">
          <Button focusKey="fav:browse" primary onEnter={() => go('/browse/movies')}>Browse movies</Button>
        </Empty>
      ) : (
        <div className="flex flex-col gap-6">
          {!filtered.length && <Empty title="No matching favorites" hint="Try a shorter name. Your favorites are still saved." />}
          {chans.length > 0 && (
            <section><h2 className="heading mb-2">Channels</h2>
              <FocusGroup focusKey="fav:channels" className="flex gap-4 flex-wrap" style={{ padding: '0.5rem' }}>
                {chans.map((t) => <ChannelTile key={t.id} tile={t} scope="fav-ch" />)}
              </FocusGroup>
            </section>
          )}
          {rest.length > 0 && (
            <section><h2 className="heading">Movies and shows</h2>
              <FocusGroup focusKey="fav:titles" className="grid-tiles">
                {rest.map((t) => <MediaTile key={t.id} tile={t} scope="fav" />)}
              </FocusGroup>
            </section>
          )}
          <FocusGroup focusKey="fav:pages" className="flex gap-3 flex-wrap items-center"><Button focusKey="fav:prev" disabled={!pageIndex} onEnter={() => setPage(pageIndex - 1)}>Previous page</Button><span className="label">{filtered.length.toLocaleString()} favorites · Page {pageIndex + 1} of {Math.max(1, Math.ceil(filtered.length / 80))}</span><Button focusKey="fav:next" disabled={(pageIndex + 1) * 80 >= filtered.length} onEnter={() => setPage(pageIndex + 1)}>Next page</Button></FocusGroup>
        </div>
      )}
    </div>
  )
}
