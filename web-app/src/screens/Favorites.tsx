import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useFavorites, useHistory, useWatchlist } from '../api/hooks'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { ChannelTile, MediaTile } from '../components/Tile'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'

// My List: what this profile saved or watched, one tab each.
const TABS = [
  { id: 'favorites', label: 'Favorites', empty: 'No favorites yet', hint: 'Open any title or channel and choose Favorite. It will show up here and on Home.' },
  { id: 'watchlist', label: 'Watchlist', empty: 'Nothing on your watchlist yet', hint: 'Open a title and choose Watchlist to keep it for later.' },
  { id: 'history', label: 'History', empty: 'Nothing watched yet', hint: 'Titles and channels you play are listed here, most recent first.' },
] as const

export function Favorites() {
  const [sp, setSp] = useSearchParams()
  const tab = TABS.find((t) => t.id === sp.get('tab')) ?? TABS[0]
  const lists = { favorites: useFavorites(), watchlist: useWatchlist(), history: useHistory() }
  const { data, error, isLoading, refetch } = lists[tab.id]
  const go = useGo()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const noun = tab.label.toLowerCase()
  const filtered = (data ?? []).filter((t) => t.title.toLowerCase().includes(search.trim().toLowerCase()))
  const pageIndex = Math.min(page, Math.max(0, Math.ceil(filtered.length / 80) - 1))
  const visible = filtered.slice(pageIndex * 80, (pageIndex + 1) * 80)
  useRestoreFocus(!!data || !!error, `fav:tab:${tab.id}`)
  const chans = visible.filter((t) => t.kind === 'channel')
  const rest = visible.filter((t) => t.kind !== 'channel')
  return (
    <div className="page-pad" data-testid="favorites">
      <h1 className="title-xl mb-3">My List</h1>
      <FocusGroup focusKey="fav:tabs" className="flex gap-2 flex-wrap mb-4" role="tablist">
        {TABS.map((t) => <Chip key={t.id} focusKey={`fav:tab:${t.id}`} pressed={tab.id === t.id} testId={`mylist-${t.id}`} onEnter={() => { setSearch(''); setPage(0); setSp(t.id === 'favorites' ? {} : { tab: t.id }, { replace: true }) }}>{t.label}</Chip>)}
      </FocusGroup>
      {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : <>
        {(data?.length ?? 0) > 12 && <div className="mb-4" style={{ maxWidth: '26rem' }}><FocusableInput focusKey="fav:search" label={`Search ${noun}`} placeholder={`Search your ${noun}`} value={search} onChange={(v) => { setSearch(v); setPage(0) }} /></div>}
        {isLoading ? <Skeleton style={{ height: '10rem' }} /> : !data?.length ? (
          <Empty title={tab.empty} hint={tab.hint}>
            <Button focusKey="fav:browse" primary onEnter={() => go('/browse/movies')}>Browse movies</Button>
          </Empty>
        ) : (
          <div className="flex flex-col gap-6">
            {!filtered.length && <Empty title={`No matching ${noun}`} hint="Try a shorter name. Everything on this list is still saved." />}
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
            {filtered.length > 80 && <FocusGroup focusKey="fav:pages" className="flex gap-3 flex-wrap items-center"><Button focusKey="fav:prev" disabled={!pageIndex} onEnter={() => setPage(pageIndex - 1)}>Previous page</Button><span className="label">{filtered.length.toLocaleString()} {noun} · Page {pageIndex + 1} of {Math.max(1, Math.ceil(filtered.length / 80))}</span><Button focusKey="fav:next" disabled={(pageIndex + 1) * 80 >= filtered.length} onEnter={() => setPage(pageIndex + 1)}>Next page</Button></FocusGroup>}
          </div>
        )}
      </>}
    </div>
  )
}
