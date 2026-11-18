import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useFavorites, useHistory, useWatchlist } from '../api/hooks'
import { Button, ChannelTile, Chip, Empty, ErrorState, GlassIconButton, MediaTile, PageHeader, Section, Skeleton } from '../ds'
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
  const pages = Math.max(1, Math.ceil(filtered.length / 80))
  return (
    <div className="page-pad lib-page" data-testid="favorites">
      <PageHeader title="My List" className="lib-head">
        <FocusGroup focusKey="fav:tabs" className="lib-chips" role="tablist">
          {TABS.map((t) => <Chip key={t.id} focusKey={`fav:tab:${t.id}`} pressed={tab.id === t.id} testId={`mylist-${t.id}`} onEnter={() => { setSearch(''); setPage(0); setSp(t.id === 'favorites' ? {} : { tab: t.id }, { replace: true }) }}>{t.label}</Chip>)}
        </FocusGroup>
      </PageHeader>
      {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : <>
        {(data?.length ?? 0) > 12 && <div className="lib-search"><FocusableInput focusKey="fav:search" label={`Search ${noun}`} placeholder={`Search your ${noun}`} value={search} onChange={(v) => { setSearch(v); setPage(0) }} /></div>}
        {isLoading ? <div className="grid-tiles lib-grid" aria-hidden="true">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="ds-skel-tile" style={{ aspectRatio: '2/3' }} />)}</div> : !data?.length ? (
          <Empty icon="bookmark" title={tab.empty} hint={tab.hint}>
            <Button focusKey="fav:browse" primary icon="film" onEnter={() => go('/browse/movies')}>Browse movies</Button>
          </Empty>
        ) : (
          <div className="lib-sections">
            {!filtered.length && <Empty icon="search" compact title={`No matching ${noun}`} hint="Try a shorter name. Everything on this list is still saved." />}
            {chans.length > 0 && (
              <Section title="Channels" meta={<span aria-hidden="true">{chans.length}</span>}>
                <FocusGroup focusKey="fav:channels" className="fav-channels">
                  {chans.map((t) => <ChannelTile key={t.id} tile={t} scope="fav-ch" />)}
                </FocusGroup>
              </Section>
            )}
            {rest.length > 0 && (
              <Section title="Movies and shows" meta={<span aria-hidden="true">{rest.length}</span>}>
                <FocusGroup focusKey="fav:titles" className="grid-tiles lib-grid">
                  {rest.map((t) => <MediaTile key={t.id} tile={t} scope="fav" />)}
                </FocusGroup>
              </Section>
            )}
            {filtered.length > 80 && <FocusGroup focusKey="fav:pages" className="lib-pager">
              <GlassIconButton focusKey="fav:prev" icon="chevL" label="Previous page" tipAt="above" disabled={!pageIndex} onEnter={() => setPage(pageIndex - 1)} />
              <span className="label lib-pager-text">{filtered.length.toLocaleString()} {noun} · Page {pageIndex + 1} of {pages}</span>
              <GlassIconButton focusKey="fav:next" icon="chevR" label="Next page" tipAt="above" disabled={(pageIndex + 1) * 80 >= filtered.length} onEnter={() => setPage(pageIndex + 1)} />
            </FocusGroup>}
          </div>
        )}
      </>}
    </div>
  )
}
