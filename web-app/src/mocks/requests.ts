import { http, HttpResponse } from 'msw'
import type { MediaRequest, Notice, RequestList, RequestManagerSettings, RequestProgress, RequestState, SourceBadge, Tile } from '../api/types'
import { profiles, seedFor, tileOf } from './fixtures'

const B = '/api/v1'
const KEY = 'mock.requests'
const OPEN: RequestState[] = ['pending_approval', 'approved', 'sent']
const ARRIVED: SourceBadge = { source_id: 's_plex', source_type: 'plex', name: 'Plex', playable_in_app: true, kind: 'library', quality: '1080p' }

type StoredNotice = Notice & { profile_id: string }
interface Store { requests: MediaRequest[]; notices: StoredNotice[]; arrived: string[]; manager: RequestManagerSettings }
// No request manager until an admin saves one in Settings, so every other fixture flow is unchanged.
const empty = (): Store => ({ requests: [], notices: [], arrived: [], manager: { url: null, api_key_set: false } })
// Kept in sessionStorage (like the accepted-disclaimer fixture) so the flow survives a reload.
function load(): Store {
  try { return { ...empty(), ...(JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as Partial<Store>) } } catch { return empty() }
}
const save = (s: Store) => sessionStorage.setItem(KEY, JSON.stringify(s))

const configured = (s: Store) => !!s.manager.url && s.manager.api_key_set
export const requestManagerConfigured = () => configured(load())

const profileOf = (req: Request) => profiles.find((p) => p.id === req.headers.get('authorization')?.replace('Bearer mock-access-', '')) ?? profiles[0]!
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('')
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const publicNotice = ({ profile_id: _owner, ...n }: StoredNotice): Notice => n
/** What the server says of a request it has not asked the request manager about: only what its state shows. */
const progressOf = (state: RequestState, at: string): RequestProgress =>
  ({ phase: state === 'pending_approval' ? 'requested' : state === 'sent' ? 'searching' : state, last_phase: null, percent: null, eta: null, detail: null, checked_at: at })

function notify(s: Store, r: MediaRequest, kind: 'request_available' | 'request_declined') {
  s.notices.push({
    id: newId(), profile_id: r.profile_id, kind, ref_id: r.title_id, created_at: new Date().toISOString(), read_at: null,
    title: kind === 'request_available' ? `${r.title} is now available` : `${r.title} was not approved`,
    body: kind === 'request_available' ? 'Ready to watch.' : r.note ?? '',
  })
}

/** Same rule as the server: request state plus whether this title can be asked for. */
export function annotate<T extends Tile>(tile: T): T {
  const s = load()
  const sources = s.arrived.includes(tile.id) && !tile.sources.some((b) => b.playable_in_app) ? [ARRIVED, ...tile.sources] : tile.sources
  const history = s.requests.filter((r) => r.title_id === tile.id)
  const current = history.find((r) => OPEN.includes(r.state)) ?? history.at(-1)
  return {
    ...tile, sources, request_state: current?.state ?? null,
    request_progress: current ? { phase: current.progress.phase, percent: current.progress.percent ?? null } : null,
    requestable: configured(s) && /^tmdb:(movie|tv):\d+$/.test(tile.id) && !sources.some((b) => b.playable_in_app),
  }
}

/** A library sync is what makes a requested title arrive, as on the server. */
export function sourceSynced() {
  const s = load()
  for (const r of s.requests.filter((x) => x.state === 'sent')) {
    r.state = 'available'
    r.updated_at = new Date().toISOString()
    r.progress = progressOf(r.state, r.updated_at)
    s.arrived.push(r.title_id)
    notify(s, r, 'request_available')
  }
  save(s)
}

export const requestHandlers = [
  http.get(`${B}/requests`, ({ request }) => {
    const me = profileOf(request)
    const q = new URL(request.url).searchParams
    const all = q.get('scope') === 'all'
    if (all && !me.is_admin) return error(403, 'request_forbidden', "You cannot see other profiles' requests.")
    const state = q.get('state')
    const requests = load().requests.filter((r) => (all || r.profile_id === me.id) && (!state || r.state === state)).reverse()
    return HttpResponse.json({ requests, can_approve: me.is_admin } satisfies RequestList)
  }),
  http.post(`${B}/requests`, async ({ request }) => {
    const me = profileOf(request)
    const b = (await request.json()) as { media_type: 'movie' | 'tv'; tmdb_id: number; seasons?: number[] | null }
    const titleId = `tmdb:${b.media_type}:${b.tmdb_id}`
    const seed = seedFor(titleId)
    if (!seed) return error(404, 'not_found', 'title not found')
    const s = load()
    const existing = s.requests.find((r) => r.title_id === titleId && OPEN.includes(r.state))
    if (existing) return HttpResponse.json(existing)
    if (!configured(s)) return error(409, 'request_manager_unconfigured', 'No request manager is configured.')
    const tile = annotate(tileOf(seed))
    if (tile.sources.some((x) => x.playable_in_app)) return error(409, 'already_available', 'This title is already available.')
    const now = new Date().toISOString()
    const created: MediaRequest = {
      id: newId(), profile_id: me.id, profile_name: me.name, media_type: b.media_type, tmdb_id: b.tmdb_id, title_id: titleId, title: seed.title, year: seed.year,
      poster: tile.poster ?? null, seasons: b.media_type === 'tv' && b.seasons?.length ? b.seasons : null,
      state: me.is_admin ? 'sent' : 'pending_approval', note: null, decided_by_profile_id: me.is_admin ? me.id : null, created_at: now, updated_at: now,
      progress: progressOf(me.is_admin ? 'sent' : 'pending_approval', now),
    }
    s.requests.push(created)
    save(s)
    return HttpResponse.json(created, { status: 201 })
  }),
  http.post(`${B}/requests/:id/:decision`, async ({ params, request }) => {
    const me = profileOf(request)
    if (params.decision !== 'approve' && params.decision !== 'decline') return error(404, 'not_found', 'no such endpoint')
    if (!me.is_admin) return error(403, 'request_forbidden', 'You cannot decide requests.')
    const s = load()
    const r = s.requests.find((x) => x.id === params.id)
    if (!r) return error(404, 'not_found', 'request not found')
    if (r.state !== 'pending_approval') return error(409, 'request_already_decided', 'This request was already decided.')
    const body = (await request.json().catch(() => null)) as { note?: string | null } | null
    r.state = params.decision === 'approve' ? 'sent' : 'declined'
    r.note = body?.note?.trim() || null
    r.decided_by_profile_id = me.id
    r.updated_at = new Date().toISOString()
    r.progress = { ...progressOf(r.state, r.updated_at), detail: r.state === 'declined' ? r.note : null }
    if (r.state === 'declined') notify(s, r, 'request_declined')
    save(s)
    return HttpResponse.json(r)
  }),
  http.get(`${B}/notices`, ({ request }) => {
    const me = profileOf(request)
    const mine = load().notices.filter((n) => n.profile_id === me.id).reverse()
    return HttpResponse.json({ notices: [...mine.filter((n) => !n.read_at), ...mine.filter((n) => n.read_at)].map(publicNotice) })
  }),
  http.post(`${B}/notices/:id/read`, ({ params, request }) => {
    const me = profileOf(request)
    const s = load()
    const n = s.notices.find((x) => x.id === params.id && x.profile_id === me.id)
    if (!n) return error(404, 'not_found', 'notice not found')
    n.read_at ??= new Date().toISOString()
    save(s)
    return HttpResponse.json(publicNotice(n))
  }),
  http.get(`${B}/settings/request_manager`, ({ request }) => (profileOf(request).is_admin ? HttpResponse.json(load().manager) : error(403, 'forbidden', 'admin only'))),
  http.put(`${B}/settings/request_manager`, async ({ request }) => {
    if (!profileOf(request).is_admin) return error(403, 'forbidden', 'admin only')
    const b = (await request.json()) as { url?: string | null; api_key?: string | null }
    const url = b.url?.trim().replace(/\/+$/, '') || null
    if (url && !/^https?:\/\/[^/@?#]+/.test(url)) return error(400, 'bad_request', 'invalid request manager url')
    // The key belongs to one server: a different URL without a new key drops it, like the real API.
    const s = load()
    s.manager = { url, api_key_set: !!url && (b.api_key ? true : b.api_key === '' ? false : s.manager.api_key_set && url === s.manager.url) }
    save(s)
    return HttpResponse.json(s.manager)
  }),
  http.post(`${B}/settings/request_manager/test`, ({ request }) => {
    if (!profileOf(request).is_admin) return error(403, 'forbidden', 'admin only')
    return HttpResponse.json(requestManagerConfigured() ? { ok: true, message: 'Connected to request manager 2.7.3.' } : { ok: false, message: 'The request manager is not configured.' })
  }),
]
