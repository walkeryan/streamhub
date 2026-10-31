import { useFavorites } from '../api/hooks'
import { Button, Empty, ErrorState, Skeleton } from '../components/ui'
import { ChannelTile, MediaTile } from '../components/Tile'
import { FocusGroup } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'

export function Favorites() {
  const { data, error, isLoading, refetch } = useFavorites()
  const go = useGo()
  useRestoreFocus(!!data, data?.length ? 'MAIN' : 'fav:browse')
  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  const chans = (data ?? []).filter((t) => t.kind === 'channel')
  const rest = (data ?? []).filter((t) => t.kind !== 'channel')
  return (
    <div className="page-pad" data-testid="favorites">
      <h1 className="display mb-4">Favorites</h1>
      {isLoading ? <Skeleton style={{ height: '10rem' }} /> : !data?.length ? (
        <Empty title="No favorites yet" hint="Open any title or channel and choose Favorite. It will show up here and on Home.">
          <Button focusKey="fav:browse" primary onEnter={() => go('/browse/movies')}>Browse movies</Button>
        </Empty>
      ) : (
        <div className="flex flex-col gap-6">
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
        </div>
      )}
    </div>
  )
}
