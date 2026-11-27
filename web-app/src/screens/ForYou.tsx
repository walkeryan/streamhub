import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { authenticatedFetch, API_BASE } from '../api/client'
import { useCreateRequest } from '../api/hooks'
import { useCan } from '../api/me'
import type { Tile } from '../api/types'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { usePlayAction } from '../components/playAction'
import { MediaTile } from '../components/Tile'
import { Button, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { Focusable, FocusableInput, FocusGroup } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { GlassIconButton, PageHeader, Section, type OverflowItem } from '../ds'
import { isOpenRequest, tmdbRef } from '../lib/requests'

type Feed = { new_episodes: Tile[]; coming_up: { show: Tile; season: number | null; episode: number | null; title: string; air_date: string }[]; movies: Tile[]; series: Tile[]; reason: string; history_used: boolean; feedback: Record<string, 'up' | 'down'> }
type Together = { code: string; member_count: number; expires_at: string; tiles: Tile[] }
async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await authenticatedFetch(new Request(new URL(API_BASE + '/for-you' + path, location.origin), {
    method, signal, headers: body === undefined ? undefined : { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  }))
  if (!res.ok) throw Object.assign(new Error(res.status === 403 || res.status === 404 ? 'This invitation is unavailable or has expired.' : 'Could not load recommendations. Try again.'), { status: res.status })
  return res.status === 204 ? undefined as T : res.json() as Promise<T>
}

export function ForYou() {
  const id = useSession(s => s.profile?.id || '')
  return <PersonalFeed key={id} profile={id} />
}
function PersonalFeed({ profile }: { profile: string }) {
  const query = useQuery({ queryKey: ['for-you', profile], queryFn: ({ signal }) => request<Feed>('?timezone=' + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone), 'GET', undefined, signal) })
  const [joint, setJoint] = useState(() => sessionStorage.getItem('sh.together:' + profile) || '')
  const [invite, setInvite] = useState(false)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const together = useQuery({ queryKey: ['for-you-together', profile, joint], enabled: !!joint, retry: false,
    queryFn: ({ signal }) => request<Together>('/together/' + encodeURIComponent(joint), 'GET', undefined, signal), refetchInterval: joint ? 5000 : false })
  const qc = useQueryClient()
  const go = useGo()
  useRestoreFocus(!query.isPending || !!joint, 'foryou:joint')
  const chooseJoint = (next: string) => {
    if (next) sessionStorage.setItem('sh.together:' + profile, next); else sessionStorage.removeItem('sh.together:' + profile)
    setJoint(next)
  }
  const participate = async (join: boolean) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await request<Together>(join ? '/together/' + encodeURIComponent(code.toUpperCase()) + '/join' : '/together', 'POST', {})
      if (useSession.getState().profile?.id !== profile) return
      qc.setQueryData(['for-you-together', profile, result.code], result); chooseJoint(result.code); setInvite(false); setCode('')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const leave = async () => {
    if (busy) return
    setBusy(true); setError('')
    try { await request('/together/' + encodeURIComponent(joint), 'DELETE'); chooseJoint('') }
    catch (e) { if ([403, 404, 410].includes((e as { status: number }).status)) chooseJoint(''); else setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const feedback = query.data?.feedback ?? {}
  const personal = (tiles: Tile[], scope: string) => <RecommendationGrid tiles={tiles.filter(t => feedback[t.id] !== 'down')} scope={scope} />
  return <div className="page-pad lib-page" data-testid="for-you">
    <PageHeader title={joint ? 'Together' : 'For You'} className="lib-head">
      <GlassIconButton focusKey="foryou:joint" icon="people" size="sm" disabled={busy} label={joint ? 'Leave group' : 'Find something together'}
        tipAt="above" tip={joint ? 'Leave group' : 'Find something together'} onEnter={() => { if (joint) void leave(); else { setError(''); setInvite(true) } }} />
    </PageHeader>
    {joint ? <>
      {together.error && <ErrorState error={together.error} onRetry={() => void together.refetch()} />}
      {together.data && <><div className="ds-panel joint-invite"><span>Invitation code</span><strong>{together.data.code}</strong><span>{together.data.member_count} {together.data.member_count === 1 ? 'viewer' : 'viewers'} joined</span></div>
        {!together.data.tiles.length ? <Empty icon="users" title={together.data.member_count < 2 ? 'Waiting for another viewer' : 'No shared picks available'} hint={together.data.member_count < 2 ? 'Share this code with someone who wants to join.' : 'There are no playable titles that meet the group’s viewing preferences.'} />
          : <Section title="Group picks"><RecommendationGrid tiles={together.data.tiles} scope="together" /></Section>}
      </>}
      {together.isPending && <Skeleton style={{ height: '8rem' }} />}{error && <p role="alert">{error}</p>}
    </> : query.error ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : !query.data ? <Skeleton style={{ height: '12rem' }} /> : <>
      <p className="muted">{query.data.reason || (query.data.history_used ? 'Based on your viewing history.' : 'Watch a title or rate a recommendation to personalize these picks.')}</p>
      {!!query.data.new_episodes.length && <Section title="New episodes">{personal(query.data.new_episodes, 'episodes')}</Section>}
      {!!query.data.coming_up.length && <Section title="Coming up"><FocusGroup focusKey="foryou:upcoming" className="credit-grid">{query.data.coming_up.map((ep, i) => <Focusable key={ep.show.id + ':' + ep.season + ':' + ep.episode + ':' + i} focusKey={'coming:' + i} className="actor-credit fx-flat" role="link" onEnter={() => go('/title/' + encodeURIComponent(ep.show.id))}>
        <span><strong>{ep.show.title}</strong><small>{[ep.season != null && ep.episode != null ? `S${ep.season} E${ep.episode}` : '', ep.title, ep.air_date].filter(Boolean).join(' · ')}</small></span></Focusable>)}</FocusGroup></Section>}
      {!!query.data.movies.length && <Section title="Movies for you">{personal(query.data.movies, 'movies')}</Section>}
      {!!query.data.series.length && <Section title="Series for you">{personal(query.data.series, 'series')}</Section>}
      {!query.data.movies.length && !query.data.series.length && !query.data.new_episodes.length && !query.data.coming_up.length && <Empty icon="film" title="Start finding your favorites" hint="Browse the library and watch a title to build your recommendations."><Button focusKey="foryou:browse" primary onEnter={() => go('/browse/movies')}>Browse movies</Button></Empty>}
    </>}
    <Modal open={invite} title="Find something together" onClose={() => setInvite(false)} firstFocusKey="joint:create" variant="sheet">
      <p>Create an invitation or enter a code from someone you want to watch with. Everyone who joins sees the group’s shared picks.</p>
      <Button focusKey="joint:create" primary disabled={busy} onEnter={() => void participate(false)}>Create invitation</Button>
      <FocusableInput focusKey="joint:code" label="Invitation code" placeholder="6-character code" maxLength={6} value={code} onChange={v => setCode(v.toUpperCase().replace(/[^A-Z0-9]/g, ''))} onSubmit={() => { if (code.length === 6) void participate(true) }} />
      {error && <p role="alert">{error}</p>}
      <div className="lib-chips"><Button focusKey="joint:join" disabled={busy || code.length !== 6} onEnter={() => void participate(true)}>Join group</Button><Button focusKey="joint:cancel" ghost onEnter={() => setInvite(false)}>Close</Button></div>
    </Modal>
  </div>
}
type GridProps = { tiles: Tile[]; scope: string }
function RecommendationGrid({ tiles, scope }: GridProps) {
  const create = useCreateRequest()
  const canRequest = useCan('requests')
  const toast = useUi(s => s.toast)
  const play = usePlayAction()
  const go = useGo()
  const [requested, setRequested] = useState<Record<string, string>>({})
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const ask = async (tile: Tile) => {
    const ref = tmdbRef(tile.id)
    if (!ref) return
    if (ref.media_type === 'tv') { go('/title/' + encodeURIComponent(tile.id)); return }
    try {
      const result = await create.mutateAsync(ref)
      if (alive.current) { setRequested(prev => ({ ...prev, [tile.id]: result.state === 'pending_approval' ? 'Waiting for approval' : 'Requested' })); toast('Requested ' + tile.title, 'success') }
    } catch (e) { toast((e as Error).message, 'error') }
  }
  return <FocusGroup focusKey={'recos:' + scope} className="grid-tiles recommendation-grid">
    {tiles.map(tile => {
      const key = scope + ':' + tile.id
      const playable = tile.sources?.some(s => s.playable_in_app)
      const requestStatus = requested[tile.id]
      const actions: OverflowItem[] = []
      if (playable) actions.push({ key: 'play', icon: tile.kind === 'show' || tile.kind === 'series' ? 'episodes' : 'play',
        label: tile.kind === 'show' || tile.kind === 'series' ? 'Episodes' : 'Play', group: 0,
        onEnter: () => { if (tile.kind === 'show' || tile.kind === 'series') go('/title/' + encodeURIComponent(tile.id)); else void play(tile) } })
      if (requestStatus) actions.push({ key: 'request-status', icon: 'check', label: requestStatus, disabled: true, group: 0, onEnter: () => undefined })
      else if (!playable && tile.requestable && tmdbRef(tile.id) && canRequest && !isOpenRequest(tile.request_state)) actions.push({
        key: 'request', icon: 'request', label: 'Request', disabled: create.isPending, group: 0, onEnter: () => void ask(tile),
      })
      return <MediaTile key={key} tile={tile} scope={scope} titleAlways mark="availability" actions={actions} />
    })}
  </FocusGroup>
}
