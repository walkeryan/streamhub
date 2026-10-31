import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { useProfilePreference } from '../api/profilePreferences'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { LanguageFilter } from '../components/LanguageFilter'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { FeatureCard } from '../components/FeatureCard'
import { useGo, useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'
import { fmtTime, useDebounced } from '../lib/util'

type SportsPage = components['schemas']['SportsPage']
const PAGE = 40
const filterSchema = z.object({ language: z.string().max(256), sport: z.string().max(256) })
const validateFilters = (raw: unknown) => { const parsed = filterSchema.safeParse(raw); return parsed.success ? parsed.data : null }

export function Sports() {
  const profile = useSession((s) => s.profile)
  const saved = useProfilePreference('sportsFilters.v1', { language: profile?.languages.length ? 'mine' : 'eng', sport: '' }, validateFilters)
  const { language, sport } = saved.value
  const setLanguage = (language: string) => saved.update((v) => ({ ...v, language }))
  const setSport = (sport: string) => saved.update((v) => ({ ...v, sport }))
  const [search, setSearch] = useState('')
  const term = useDebounced(search, 300)
  const [page, setPage] = useState(0)
  const filter = `${profile?.id}|${profile?.languages.join(',')}|${language}|${sport}|${term}`
  const [previousFilter, setPreviousFilter] = useState(filter)
  if (previousFilter !== filter) { setPreviousFilter(filter); setPage(0) }
  const q = useQuery({
    queryKey: ['sports', profile?.id, filter, page],
    enabled: saved.ready && !!profile,
    queryFn: () => unwrap<SportsPage>(api.GET('/sports', { params: { query: {
      language: language === 'mine' ? profile?.languages.join(',') || 'all' : language,
      sport: sport || undefined, q: term || undefined, limit: PAGE, offset: page * PAGE,
    } } })),
    refetchInterval: 30_000,
  })
  const go = useGo()
  useRestoreFocus(saved.ready && !!q.data, 'sports:search')
  const data = q.data
  if (data && page > Math.max(0, Math.ceil(data.total / PAGE) - 1)) setPage(Math.max(0, Math.ceil(data.total / PAGE) - 1))
  if (!saved.ready) return <div className="page-pad" data-testid="sports"><h1 className="display">Sports on now</h1>{saved.error ? <><p role="alert">{saved.error}</p><Button focusKey="sports:retry-prefs" onEnter={() => void saved.retry()}>Retry</Button></> : <p role="status">Loading saved filters…</p>}</div>
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
    <p className="label mb-3" role="status">{saved.error ?? (saved.saving ? 'Saving filters…' : 'Filter changes sync with this profile.')}</p>
    {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : q.isPending ? <Skeleton style={{ height: '16rem' }} /> : data && <>
      <p className="muted mb-4" role="status">{data.coverage.channels_with_current_schedule.toLocaleString()} of {data.coverage.eligible_channels.toLocaleString()} eligible channels have a current schedule. Missing or unclassified guide data may omit sports. Updated {fmtTime(data.as_of)} · times are local.</p>
      {!data.items.length ? <Empty title="No matching sports listings" hint="Try All sports, another language or a shorter search. No listings does not mean no sport is airing." /> :
        <FocusGroup focusKey="sports:list" className="feature-grid" label="Current sports listings">
          {data.items.map((item) => <article key={item.id} data-testid="sports-listing">
            <FeatureCard role="button" focusKey={`sports:watch:${item.id}`} label={`Watch ${item.channel_name}`}
              title={item.title} eyebrow={`${item.channel_name} · ${fmtTime(item.start)}–${fmtTime(item.stop)}`}
              fallbackName={item.channel_name} badge="On now" onEnter={() => go(`/play/${encodeURIComponent(item.channel_id)}`)}>
              <p className="label">{item.sport} · {item.source_name}</p>
            </FeatureCard>
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
