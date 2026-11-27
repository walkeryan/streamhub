import { http, HttpResponse } from 'msw'
import { requestId } from '../cast/requestId'
import { titles, watch } from './fixtures'
import { annotate } from './requests'
import type { CastCommand, CastPlayback } from '../player/castNative'
import type { CastSession } from '../cast/controller'

const base = '/api/v1'
const fail = (status: number) => HttpResponse.json({ message: 'Fixture resource is unavailable.' }, { status })
const read = <T>(key: string, fallback: T): T => JSON.parse(localStorage.getItem('mock.viewing:' + key) ?? JSON.stringify(fallback)) as T
const write = (key: string, value: unknown) => localStorage.setItem('mock.viewing:' + key, JSON.stringify(value))
export const mockWatchlist = (profile: string): Set<string> => new Set(read<string[]>('watchlist:' + profile, [...watch]))
export const writeMockWatchlist = (profile: string, value: Set<string>) => write('watchlist:' + profile, [...value])
const emptyPlayback: CastPlayback = { id: null, title: '', poster: null, playing: false, position_ms: 0, duration_ms: null, live: false }
const device = () => {
  const key = 'mock.viewing.device'
  let id = sessionStorage.getItem(key)
  if (!id) { id = requestId(); sessionStorage.setItem(key, id) }
  return id
}
type Group = { members: string[]; expires_at: string }
type Receiver = { id: string; name: string; seen: number; playback: CastPlayback; link: CastSession | null; sender: string | null; commands: CastCommand[] }
export function viewingHandlers(profile: (r: Request) => string) {
  const targets = (r: Request) => read<Receiver[]>('targets:' + profile(r), []).filter(t => Date.now() - t.seen < 30_000)
  const save = (r: Request, all: Receiver[]) => write('targets:' + profile(r), all)
  const group = (code: string, g: Group) => ({ code, member_count: g.members.length, expires_at: g.expires_at,
    tiles: g.members.length >= 2 ? titles.filter(t => t.sources.some(s => s.playable_in_app)).slice(0, 4).map(annotate) : [] })
  return [
    http.get(base + '/for-you', ({ request }) => {
      const feedback = read<Record<string, 'up'|'down'>>('votes:' + profile(request), {})
      const visible = titles.filter(t => feedback[t.id] !== 'down')
      return HttpResponse.json({ movies: visible.filter(t => t.kind === 'movie').slice(0, 8).map(annotate), series: visible.filter(t => t.kind === 'show').slice(0, 6).map(annotate), new_episodes: [], coming_up: [], history_used: false, reason: 'Popular picks. Rate a title to personalize your recommendations.', feedback })
    }),
    http.get(base + '/for-you/feedback', ({ request }) => {
      const id = new URL(request.url).searchParams.get('id') ?? ''
      const viewer = profile(request)
      const votes = read<Record<string, 'up'|'down'>>('votes:' + viewer, {})
      return HttpResponse.json({ id, vote: votes[id] ?? null, saved: mockWatchlist(viewer).has(id) })
    }),
    http.put(base + '/for-you/feedback', async ({ request }) => {
      const body = await request.json() as { id: string; vote: 'up'|'down'|null }
      const key = 'votes:' + profile(request), votes = read<Record<string,string>>(key, {})
      if (!titles.some(t => t.id === body.id)) return fail(404)
      if (body.vote) votes[body.id] = body.vote; else delete votes[body.id]
      write(key, votes); return HttpResponse.json(body)
    }),
    http.delete(base + '/library/media', ({ request }) => {
      const id = new URL(request.url).searchParams.get('id') ?? ''
      const tile = titles.find(t => t.id === id)
      if (profile(request) !== 'p1') return fail(403)
      if (!tile?.sources.some(source => source.playable_in_app)) return fail(404)
      const removed = new Set(read<string[]>('library:removed', [])); removed.add(id); write('library:removed', [...removed])
      return new HttpResponse(null, { status: 204 })
    }),
    http.put(base + '/library/media', ({ request }) => {
      const id = new URL(request.url).searchParams.get('id') ?? ''
      if (profile(request) !== 'p1') return fail(403)
      const removed = new Set(read<string[]>('library:removed', [])); removed.delete(id); write('library:removed', [...removed])
      return new HttpResponse(null, { status: 204 })
    }),
    http.post(base + '/for-you/together', ({ request }) => {
      const code = requestId().slice(0, 6).toUpperCase(), g = { members: [profile(request)], expires_at: new Date(Date.now() + 3600000).toISOString() }
      write('group:' + code, g); return HttpResponse.json(group(code, g))
    }),
    http.post(base + '/for-you/together/:code/join', ({ request, params }) => {
      const code = String(params.code), g = read<Group|null>('group:' + code, null)
      if (!g || Date.parse(g.expires_at) <= Date.now()) return fail(404)
      const viewer = profile(request)
      if (!g.members.includes(viewer)) { g.members.push(viewer); write('group:' + code, g) }
      return HttpResponse.json(group(code, g))
    }),
    http.all(base + '/for-you/together/:code', ({ request, params }) => {
      const code = String(params.code), g = read<Group|null>('group:' + code, null), viewer = profile(request)
      if (!g || Date.parse(g.expires_at) <= Date.now()) return fail(404)
      if (!g.members.includes(viewer)) return fail(403)
      if (request.method === 'DELETE') { g.members = g.members.filter(m => m !== viewer); write('group:' + code, g.members.length ? g : null); return new HttpResponse(null, { status: 204 }) }
      return HttpResponse.json(group(code, g))
    }),
    http.get(base + '/people/:id/credits', ({ params, request }) => {
      if (params.id !== '72') return fail(404)
      const kind = new URL(request.url).searchParams.get('kind')
      const credits = titles.filter(t => ['tmdb:movie:101', 'tmdb:tv:201'].includes(t.id)).map(t => ({
        id: t.id, title: t.title, kind: t.kind === 'show' ? 'tv' : 'movie', year: t.year ?? null, poster: t.poster, roles: ['Fixture lead'],
      }))
      return HttpResponse.json({ name: 'Casey Vale', photo: null, known_for: credits.slice(0, 1), credits: credits.filter(c => kind === 'all' || !kind || c.kind === kind), page: 1, has_more: false })
    }),
    http.get(base + '/cast/targets', ({ request }) => HttpResponse.json({ targets: targets(request).filter(t => t.id !== device() && !t.link).map(({id,name}) => ({id,name})) })),
    http.get(base + '/cast/session', ({ request }) => HttpResponse.json({ session: targets(request).find(t => t.sender === device())?.link ?? null })),
    http.post(base + '/cast/connect', async ({ request }) => {
      const { target_id } = await request.json() as { target_id: string }; const all = targets(request), t = all.find(i => i.id === target_id)
      if (!t || t.link) return fail(409)
      t.sender = device(); t.commands = []; t.link = { id: requestId(), target_id, target_name: t.name, status: 'pending', playback: t.playback, last_ack: 0, last_error: null }
      save(request, all); return HttpResponse.json({ session: t.link })
    }),
    http.post(base + '/cast/commands', async ({ request }) => {
      const c = await request.json() as CastCommand & { session_id: string }
      const all = targets(request), t = all.find(i => i.sender === device() && i.link?.id === c.session_id && i.link.status === 'connected')
      if (!t) return fail(403)
      const exists = t.commands.find(i => i.request_id === c.request_id)
      if (exists) return HttpResponse.json({seq:exists.seq})
      const seq = t.commands.length + 1; t.commands.push({...c, seq}); save(request, all); return HttpResponse.json({seq})
    }),
    http.delete(base + '/cast/session/:id', ({ request, params }) => {
      const all = targets(request), t = all.find(i => i.sender === device() && i.link?.id === params.id)
      if (!t) return fail(404)
      t.link = null; t.sender = null; save(request, all); return new HttpResponse(null, {status:204})
    }),
    http.post(base + '/cast/receiver/sync', async ({ request }) => {
      const b = await request.json() as { enabled: boolean; name?: string; playback?: CastPlayback; ack?: number; session_id?:string; error?:string }
      const all = targets(request), id = device()
      const t = all.find(i => i.id === id) ?? { id, name:'Fixture TV', seen:Date.now(), playback: emptyPlayback, link:null, sender:null, commands:[] }
      if (!b.enabled) { save(request, all.filter(i => i.id !== id)); return HttpResponse.json({session:null,controller_name:null,command:null}) }
      t.seen = Date.now(); if (b.name) t.name = b.name; if (b.playback) t.playback = b.playback
      if (t.link) { t.link.playback = t.playback; if (t.link.id === b.session_id && b.ack && b.ack >= t.link.last_ack) { t.link.last_ack = b.ack; t.link.last_error = b.error ?? null } }
      save(request, [...all.filter(i => i.id !== id),t])
      return HttpResponse.json({session:t.link,controller_name:t.sender ? 'Fixture phone' : null,command:t.link?.status === 'connected' ? t.commands.find(c => c.seq > t.link!.last_ack) ?? null : null})
    }),
    http.post(base + '/cast/receiver/decision', async ({ request }) => {
      const b = await request.json() as {session_id:string;accept:boolean}, all = targets(request), t = all.find(i => i.id === device() && i.link?.id === b.session_id)
      if (!t?.link) return fail(404)
      if (b.accept) t.link.status = 'connected'; else { t.link = null; t.sender = null }
      save(request, all); return new HttpResponse(null,{status:204})
    }),
  ]
}
