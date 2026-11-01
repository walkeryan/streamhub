import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useSearch } from '../api/hooks'
import { LanguageFilter } from '../components/LanguageFilter'
import { useSession } from '../store/session'
import { Attribution, Button, Chip, Empty, ErrorState } from '../components/ui'
import { MediaTile } from '../components/Tile'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus, useGo } from '../nav/back'
import { useUi } from '../store/ui'
import { useDebounced } from '../lib/util'
import type { Tile } from '../api/types'

export function Search() {
  const profileId = useSession((s) => s.profile?.id ?? 'anonymous')
  return <ProfileSearch key={profileId} profileId={profileId} />
}

function ProfileSearch({ profileId }: { profileId: string }) {
  const [params, setParams] = useSearchParams()
  const storageKey = `sh.search.${profileId}`
  const [saved] = useState(() => { try { return JSON.parse(sessionStorage.getItem(storageKey) ?? '{}') as { q?: string; language?: string } } catch { return {} } })
  const [q, setQ] = useState((params.get('profile') === profileId ? params.get('q') : null) ?? saved.q ?? '')
  const dq = useDebounced(q, 400)
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  const [language, setLanguage] = useState((params.get('profile') === profileId ? params.get('language') : null) ?? saved.language ?? (preferred ? 'mine' : 'all'))
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ q, language })) } catch { /* Storage may be disabled. URL state still preserves navigation. */ }
    if (params.get('q') !== q || params.get('language') !== language || params.get('profile') !== profileId) {
      setParams({ q, language, profile: profileId }, { replace: true })
    }
  }, [q, language, profileId, storageKey, params, setParams])
  const { data, error, isFetching, refetch } = useSearch(dq, { language: language === 'mine' ? preferred || 'all' : language })
  const recents = useUi((s) => s.recentSearches)
  const addSearch = useUi((s) => s.addSearch)
  const clear = useUi((s) => s.clearSearches)
  const go = useGo()
  const input = useRef<HTMLInputElement>(null)
  // wait for the (cached) results so the remembered result tile exists before focus is restored to it
  useRestoreFocus(dq.trim().length < 2 || !!data || !!error, 'search:input')

  useEffect(() => { if (data && dq.trim().length > 1) addSearch(dq.trim()) }, [data, dq, addSearch])

  const playHere = data?.play_here ?? []
  const openIn = data?.open_in_app ?? []
  const chans = data?.channels ?? []
  const ranked = data?.results
  const count = ranked ? ranked.length : playHere.length + openIn.length + chans.length
  const none = !!data && count === 0
  const tiles = (list: Tile[], scope: string) => (
    <FocusGroup focusKey={`search:${scope}`} className="grid-tiles">
      {list.map((t) => <MediaTile key={t.id} tile={t} scope={scope} />)}
    </FocusGroup>
  )

  return (
    <div className="page-pad search-layout" data-testid="search">
      <FocusGroup focusKey="search:left" className="search-controls flex flex-col gap-4" data-testid="search-controls">
        <h1 className="title-xl">Search</h1>
        <FocusableInput focusKey="search:input" label="Search titles and channels" placeholder="Titles, shows, channels" value={q} onChange={setQ}
          inputRef={input} inputMode="search" onSubmit={() => setQ((v) => v.trim())} />
        <LanguageFilter scope="search" value={language} onChange={setLanguage} />
        {!q && recents.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between"><span className="label">Recent searches</span><Button focusKey="search:clear" ghost onEnter={clear}>Clear</Button></div>
            <div className="flex gap-2 flex-wrap">{recents.map((r) => <Chip key={r} focusKey={`recent:${r}`} onEnter={() => setQ(r)}>{r}</Chip>)}</div>
          </div>
        )}
        <p className="label" aria-live="polite">{isFetching ? 'Searching…' : dq.trim().length > 1 && data ? `${count} results` : 'Type at least 2 letters.'}</p>
      </FocusGroup>
      <div className="min-w-0" data-testid="search-results" style={{ minHeight: '20rem' }}>
        {data?.discovery_note && <p className="label mb-4">{data.discovery_note}</p>}
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : none ? (
          <Empty title={`No results for “${dq}”`} hint="Try a shorter title or All languages to include other and unknown languages.">
            <Button focusKey="search:all" onEnter={() => setLanguage('all')}>Search all languages</Button>
            <Button focusKey="search:add" icon="plus" onEnter={() => go('/sources')}>Add a source</Button>
          </Empty>
        ) : (
          <>
            {ranked && ranked.length > 0 && <section data-testid="group-relevance"><h2 className="heading">Best matches</h2>{tiles(ranked, 'relevance')}</section>}
            {!ranked && playHere.length > 0 && <section data-testid="group-play"><h2 className="heading">Play here</h2>{tiles(playHere, 'play-here')}</section>}
            {!ranked && openIn.length > 0 && <section data-testid="group-open"><h2 className="heading">Open in app</h2>{tiles(openIn, 'open-in-app')}</section>}
            {!ranked && chans.length > 0 && (
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
