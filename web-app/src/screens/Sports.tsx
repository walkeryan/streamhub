import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { LanguageFilter } from '../components/LanguageFilter'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'
import { fmtTime, useDebounced } from '../lib/util'

type SportsPage = components['schemas']['SportsPage']
const PAGE = 40

export function Sports() {
  const profile = useSession((s) => s.profile)
  const [language, setLanguage] = useState(profile?.languages.length ? 'mine' : 'eng')
  const [sport, setSport] = useState('')
  const [search, setSearch] = useState('')
  const term = useDebounced(search, 300)
  const [page, setPage] = useState(0)
  const filter = `${profile?.id}|${profile?.languages.join(',')}|${language}|${sport}|${term}`
  const [previousFilter, setPreviousFilter] = useState(filter)
  if (previousFilter !== filter) { setPreviousFilter(filter); setPage(0) }
  const q = useQuery({
    queryKey: ['sports', profile?.id, filter, page],
    queryFn: () => unwrap<SportsPage>(api.GET('/sports', { params: { query: {
      language: language === 'mine' ? profile?.languages.join(',') || 'all' : language,
      sport: sport || undefined, q: term || undefined, limit: PAGE, offset: page * PAGE,
    } } })),
    refetchInterval: 30_000,
  })
  const go = useGo()
  useRestoreFocus(!!q.data, 'sports:search')
  const data = q.data
  if (data && page > Math.max(0, Math.ceil(data.total / PAGE) - 1)) setPage(Math.max(0, Math.ceil(data.total / PAGE) - 1))
  return <div className="page-pad" data-testid="sports">
    <h1 className="display mb-2">Sports on now</h1>
    <p className="muted mb-4">Sports programmes airing now according to your guide. Listings can include games, replays, highlights and studio shows. Each channel feed is shown separately.</p>
    <div className="flex flex-col gap-3 mb-4">
      <FocusableInput focusKey="sports:search" label="Search sports programmes or channels" value={search} onChange={setSearch} placeholder="Programme or channel name" />
      <LanguageFilter scope="sports" value={language} onChange={setLanguage} />
      <FocusGroup focusKey="sports:types" className="flex flex-wrap gap-2" label="Sport type">
        <Chip focusKey="sports:all" pressed={!sport} onEnter={() => setSport('')}>All sports</Chip>
        {(data?.sports ?? []).map((kind) => <Chip key={kind.name} focusKey={`sports:type:${kind.name}`} pressed={sport === kind.name} onEnter={() => setSport(kind.name)}>{kind.name} · {kind.count}</Chip>)}
        {sport && !data?.sports.some((kind) => kind.name === sport) && <Chip focusKey="sports:selected" pressed onEnter={() => setSport('')}>{sport} · Clear</Chip>}
      </FocusGroup>
    </div>
    {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : q.isPending ? <Skeleton style={{ height: '16rem' }} /> : data && <>
      <p className="muted mb-4" role="status">{data.coverage.channels_with_current_schedule.toLocaleString()} of {data.coverage.eligible_channels.toLocaleString()} eligible channels have a current schedule. Missing or unclassified guide data may omit sports. Updated {fmtTime(data.as_of)} · times are local.</p>
      {!data.items.length ? <Empty title="No matching sports listings" hint="Try All sports, another language or a shorter search. No listings does not mean no sport is airing." /> :
        <FocusGroup focusKey="sports:list" className="flex flex-col gap-3" label="Current sports listings">
          {data.items.map((item) => <article key={item.id} data-testid="sports-listing" style={{ padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--t-text-muted)', background: 'var(--t-surface)' }}>
            <div className="flex gap-3 flex-wrap items-center" style={{ justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0, flex: '1 1 15rem' }}>
                <p className="label muted">{item.sport} · {fmtTime(item.start)}–{fmtTime(item.stop)}</p>
                <h2 className="heading" style={{ overflowWrap: 'anywhere' }}>{item.title}</h2>
                <p className="muted" style={{ overflowWrap: 'anywhere' }}>{item.channel_number} · {item.channel_name} · {item.source_name}</p>
              </div>
              <Button focusKey={`sports:watch:${item.id}`} primary label={`Watch ${item.channel_name}`} onEnter={() => go(`/play/${encodeURIComponent(item.channel_id)}`)}>Watch channel</Button>
            </div>
          </article>)}
        </FocusGroup>}
      <div className="flex gap-3 items-center flex-wrap mt-4">
        <Button focusKey="sports:prev" disabled={!page} onEnter={() => setPage(page - 1)}>Previous</Button>
        <span>{data.total.toLocaleString()} {data.total === 1 ? 'listing' : 'listings'} · page {page + 1} of {Math.max(1, Math.ceil(data.total / PAGE))}</span>
        <Button focusKey="sports:next" disabled={(page + 1) * PAGE >= data.total} onEnter={() => setPage(page + 1)}>Next</Button>
      </div>
    </>}
  </div>
}
