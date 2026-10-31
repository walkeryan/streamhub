import { useEffect, useRef, useState } from 'react'
import { useSearch } from '../api/hooks'
import { Attribution, Button, Chip, Empty, ErrorState } from '../components/ui'
import { MediaTile } from '../components/Tile'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'
import { useUi } from '../store/ui'
import { useDebounced } from '../lib/util'
import type { Tile } from '../api/types'

export function Search() {
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 400)
  const { data, error, isFetching, refetch } = useSearch(dq)
  const recents = useUi((s) => s.recentSearches)
  const addSearch = useUi((s) => s.addSearch)
  const clear = useUi((s) => s.clearSearches)
  const go = useGo()
  const input = useRef<HTMLInputElement>(null)
  useRestoreFocus(true, 'search:input')

  useEffect(() => { if (data && dq.trim().length > 1) addSearch(dq.trim()) }, [data, dq, addSearch])

  const playHere = data?.play_here ?? []
  const openIn = data?.open_in_app ?? []
  const chans = data?.channels ?? []
  const none = !!data && !playHere.length && !openIn.length && !chans.length
  const tiles = (list: Tile[], scope: string) => (
    <FocusGroup focusKey={`search:${scope}`} className="grid-tiles">
      {list.map((t) => <MediaTile key={t.id} tile={t} scope={scope} />)}
    </FocusGroup>
  )

  return (
    <div className="page-pad flex gap-8 flex-wrap md:flex-nowrap" data-testid="search" style={{ alignItems: 'flex-start' }}>
      <FocusGroup focusKey="search:left" className="flex flex-col gap-4" style={{ width: 'min(22rem, 100%)', flex: 'none' }}>
        <h1 className="title-xl">Search</h1>
        <FocusableInput focusKey="search:input" label="Search titles and channels" placeholder="Titles, shows, channels" value={q} onChange={setQ}
          inputRef={input} inputMode="search" onSubmit={() => setQ((v) => v.trim())} />
        {!q && recents.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between"><span className="label">Recent searches</span><Button focusKey="search:clear" ghost onEnter={clear}>Clear</Button></div>
            <div className="flex gap-2 flex-wrap">{recents.map((r) => <Chip key={r} focusKey={`recent:${r}`} onEnter={() => setQ(r)}>{r}</Chip>)}</div>
          </div>
        )}
        <p className="label" aria-live="polite">{isFetching ? 'Searching…' : dq.trim().length > 1 && data ? `${playHere.length + openIn.length + chans.length} results` : 'Type at least 2 letters.'}</p>
      </FocusGroup>
      <div className="flex-1 min-w-0" style={{ minHeight: '20rem' }}>
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : none ? (
          <Empty title={`No results for “${dq}”`} hint="Check the spelling, or add more sources so there is more to search.">
            <Button focusKey="search:add" icon="plus" onEnter={() => go('/sources')}>Add a source</Button>
          </Empty>
        ) : (
          <>
            {playHere.length > 0 && <section data-testid="group-play"><h2 className="heading">Play here</h2>{tiles(playHere, 'play-here')}</section>}
            {openIn.length > 0 && <section data-testid="group-open"><h2 className="heading">Open in app</h2>{tiles(openIn, 'open-in-app')}</section>}
            {chans.length > 0 && (
              <section data-testid="group-channels">
                <h2 className="heading mb-2">Channels</h2>
                <FocusGroup focusKey="search:channels" className="flex flex-col gap-2" role="listbox">
                  {chans.map((c) => <ChannelRow key={c.id} focusKey={`search-ch::${c.id}`} channel={c} onEnter={() => go(`/play/${encodeURIComponent(c.id)}`)} />)}
                </FocusGroup>
              </section>
            )}
            {data && <div className="mt-8"><Attribution text={data.attribution ?? 'Availability data by JustWatch'} /></div>}
          </>
        )}
      </div>
    </div>
  )
}
