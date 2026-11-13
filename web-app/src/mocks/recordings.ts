import { http, HttpResponse } from 'msw'
import type { Channel, PlaybackDescriptor, Programme, Recording, RecordingList, RecordingRule, RecordingRuleList } from '../api/types'
import { channels, profiles, programmesFor, teamsOf } from './fixtures'
import { holds, legacyServer, refusedWithout } from './people'
import { sportsChannels } from './sports'

/**
 * A fake recorder: a schedule and rules, no capture. Listing needs recordings.view, changing anything
 * recordings.schedule. It starts empty, so every other fixture flow is what it was. `sessionStorage['mock.recordings.demo'] = '1'` starts it with a little of everything
 * instead (two to come, one being recorded, two finished, one failed, one missed, two rules) on the
 * first channels of the fixture guide; `sessionStorage['mock.recordings.off'] = '1'` is a server
 * without a recordings folder.
 */
const B = '/api/v1'
const KEY = 'mock.recordings'
const OFF = 'mock.recordings.off'
const DEMO = 'mock.recordings.demo'
const MAX_RULES = 50
const HOUR = 3600_000

interface Store { recordings: Recording[]; rules: RecordingRule[] }

const profileOf = (req: Request) => profiles.find((p) => p.id === req.headers.get('authorization')?.replace('Bearer mock-access-', '')) ?? profiles[0]!
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('')
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const enabled = () => sessionStorage.getItem(OFF) !== '1'
const isSport = (p: Programme) => !!p.is_sport
const plain = (title: string) => title.replace(/\s*\(live\)\s*$/i, '').trim().toLowerCase()

function recordingFor(p: Programme, ch: Channel, state: Recording['state'], extra: Partial<Recording> = {}): Recording {
  const now = new Date().toISOString()
  return {
    id: newId(), profile_id: 'p1', profile_name: 'Owner', rule_id: null, channel_id: ch.id, channel_name: ch.name, title: p.title, subtitle: null, season: null, episode: null,
    is_sport: isSport(p), teams: isSport(p) ? teamsOf(p.title) : [], starts_at: p.start, ends_at: p.stop, pad_before_s: 120, pad_after_s: isSport(p) ? 3600 : 300, state, error: null,
    size_bytes: state === 'done' ? Math.round(((Date.parse(p.stop) - Date.parse(p.start)) / HOUR) * 1.8e9) : null, created_at: now, updated_at: now, ...extra,
  }
}

/** A channel's schedule as the fixture guide and channel list show it now: both start three hours back. */
const guide = (ch: Channel) => programmesFor(ch, Date.now() - 3 * HOUR, Date.now() + 9 * HOUR)
/** Programmes as last served, by id: the fixture schedule is only stable within one answer. */
const served = new Map<string, Programme>()

function seed(): Store {
  const now = Date.now()
  const at = (i: number, offset: number) => {
    const list = guide(channels[i]!)
    const current = list.findIndex((p) => Date.parse(p.start) <= now && Date.parse(p.stop) > now)
    return list[current + offset]!
  }
  const series: RecordingRule = { id: newId(), profile_id: 'p1', profile_name: 'Owner', kind: 'series', match: 'Evening Edition', channel_id: channels[0]!.id, channel_name: channels[0]!.name, new_only: true, keep: 5, enabled: true, created_at: new Date(now - 72 * HOUR).toISOString() }
  const team: RecordingRule = { id: newId(), profile_id: 'p1', profile_name: 'Owner', kind: 'team', match: 'Riverton Rovers', channel_id: null, channel_name: null, new_only: false, keep: null, enabled: false, created_at: new Date(now - 48 * HOUR).toISOString() }
  return {
    recordings: [
      recordingFor(at(4, -2), channels[4]!, 'missed', { error: 'No connection to the provider was free for ten minutes.' }),
      recordingFor(at(3, -1), channels[3]!, 'failed', { error: 'The disk fell below the free space the server keeps.' }),
      recordingFor(at(0, -1), channels[0]!, 'done', { rule_id: series.id, subtitle: 'The Harbor Vote', season: 3, episode: 7 }),
      recordingFor(at(2, -1), channels[2]!, 'done'),
      recordingFor(at(1, 0), channels[1]!, 'recording'),
      recordingFor(at(0, 2), channels[0]!, 'scheduled', { rule_id: series.id }),
      recordingFor(at(5, 1), channels[5]!, 'scheduled'),
    ],
    rules: [series, team],
  }
}
function load(): Store {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Store
  } catch { /* start over */ }
  const fresh = sessionStorage.getItem(DEMO) === '1' ? seed() : { recordings: [], rules: [] }
  save(fresh)
  return fresh
}
// Kept in sessionStorage (like the request fixtures) so a schedule survives a reload.
const save = (s: Store) => sessionStorage.setItem(KEY, JSON.stringify(s))

/** The state the guide shows on a programme: the household's recording of that channel and start, if any. */
export function withRecordingState(p: Programme): Programme {
  served.set(p.id, p)
  if (!enabled()) return { ...p, recording_state: null }
  const found = load().recordings.find((r) => r.channel_id === p.channel_id && r.state !== 'cancelled' && Date.parse(r.starts_at) === Date.parse(p.start))
  return { ...p, recording_state: found?.state ?? null }
}
export const withRecordingStates = (c: Channel): Channel => ({ ...c, now: c.now ? withRecordingState(c.now) : c.now, next: c.next ? withRecordingState(c.next) : c.next })

const matches = (rule: RecordingRule, p: Programme) =>
  rule.kind === 'series' ? plain(p.title) === plain(rule.match) : rule.kind === 'team' ? isSport(p) && plain(p.title).includes(plain(rule.match)) : plain(p.title).includes(plain(rule.match))

/** What a rule does when it is made, switched on or changed: schedule the matching programmes still to come. */
function applyRule(s: Store, rule: RecordingRule): void {
  const now = Date.now()
  let added = 0
  for (const ch of rule.channel_id ? channels.filter((c) => c.id === rule.channel_id) : channels.slice(0, 12)) {
    for (const p of guide(ch)) {
      if (added >= 4) return
      if (Date.parse(p.start) <= now || !matches(rule, p)) continue
      if (s.recordings.some((r) => r.channel_id === ch.id && r.state !== 'cancelled' && Date.parse(r.starts_at) === Date.parse(p.start))) continue
      s.recordings.push(recordingFor(p, ch, 'scheduled', { rule_id: rule.id, profile_id: rule.profile_id, profile_name: rule.profile_name }))
      added++
    }
  }
}
const dropUpcoming = (s: Store, ruleId: string) => { s.recordings = s.recordings.filter((r) => !(r.rule_id === ruleId && r.state === 'scheduled')) }
const mayChange = (req: Request) => holds(req, 'recordings.schedule')
const refuse = (req: Request) => !enabled() ? error(409, 'recording_unavailable', 'Recording is not set up on this server.') : !mayChange(req) ? error(403, 'recording_forbidden', 'This profile is not allowed to schedule recordings.') : null

export const recordingHandlers = [
  http.get(`${B}/recordings`, ({ request }) => {
    if (legacyServer()) return error(404, 'not_found', 'Not found')
    const barred = refusedWithout(request, 'recordings.view')
    if (barred) return barred
    if (!enabled()) return HttpResponse.json({ enabled: false, can_schedule: mayChange(request), recordings: [] } satisfies RecordingList)
    const state = new URL(request.url).searchParams.get('state')
    const recordings = load().recordings.filter((r) => (state ? r.state === state : r.state !== 'cancelled')).sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))
    return HttpResponse.json({ enabled: true, can_schedule: mayChange(request), recordings } satisfies RecordingList)
  }),
  http.post(`${B}/recordings`, async ({ request }) => {
    const refused = refuse(request)
    if (refused) return refused
    const me = profileOf(request)
    const b = (await request.json()) as { programme_id: string; channel_id?: string | null; start?: string | null }
    const ch = [...channels, ...sportsChannels].find((c) => c.id === b.channel_id)
    // As on the server: the id first, else the same channel and start.
    const p = served.get(b.programme_id) ?? (ch && guide(ch).find((x) => Date.parse(x.start) === Date.parse(b.start ?? '')))
    if (!ch || !p) return error(409, 'guide_changed', 'The guide has changed. Open the programme again and record it from there.')
    if (Date.parse(p.stop) <= Date.now()) return error(409, 'programme_ended', 'This programme has already ended.')
    const s = load()
    const existing = s.recordings.find((r) => r.channel_id === ch.id && r.state !== 'cancelled' && Date.parse(r.starts_at) === Date.parse(p.start))
    if (existing) return existing.profile_id === me.id ? HttpResponse.json(existing) : error(409, 'already_scheduled', `${existing.profile_name ?? 'Someone else'} is already recording this programme.`)
    const created = recordingFor(p, ch, Date.parse(p.start) <= Date.now() ? 'recording' : 'scheduled', { profile_id: me.id, profile_name: me.name })
    s.recordings.push(created)
    save(s)
    return HttpResponse.json(created, { status: 201 })
  }),
  http.delete(`${B}/recordings/:id`, ({ params, request }) => {
    const refused = refuse(request)
    if (refused) return refused
    const s = load()
    if (!s.recordings.some((r) => r.id === params.id)) return error(404, 'not_found', 'Recording not found.')
    s.recordings = s.recordings.filter((r) => r.id !== params.id)
    save(s)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(`${B}/recording-rules`, ({ request }) =>
    refusedWithout(request, 'recordings.view') ?? HttpResponse.json({ enabled: enabled(), can_schedule: mayChange(request), rules: enabled() ? load().rules : [] } satisfies RecordingRuleList)),
  http.post(`${B}/recording-rules`, async ({ request }) => {
    const refused = refuse(request)
    if (refused) return refused
    const me = profileOf(request)
    const b = (await request.json()) as { kind: RecordingRule['kind']; match: string; channel_id?: string | null; new_only?: boolean; keep?: number | null }
    const s = load()
    const same = s.rules.find((r) => r.profile_id === me.id && r.kind === b.kind && plain(r.match) === plain(b.match) && (r.channel_id ?? null) === (b.channel_id ?? null))
    if (same) return HttpResponse.json(same)
    if (s.rules.length >= MAX_RULES) return error(409, 'rule_limit', `A profile can have at most ${MAX_RULES} recording rules. Delete one first.`)
    const rule: RecordingRule = {
      id: newId(), profile_id: me.id, profile_name: me.name, kind: b.kind, match: b.match.trim(), channel_id: b.channel_id ?? null,
      channel_name: channels.find((c) => c.id === b.channel_id)?.name ?? null, new_only: !!b.new_only, keep: b.keep ?? null, enabled: true, created_at: new Date().toISOString(),
    }
    s.rules.push(rule)
    applyRule(s, rule)
    save(s)
    return HttpResponse.json(rule, { status: 201 })
  }),
  http.patch(`${B}/recording-rules/:id`, async ({ params, request }) => {
    const refused = refuse(request)
    if (refused) return refused
    const s = load()
    const rule = s.rules.find((r) => r.id === params.id)
    if (!rule) return error(404, 'not_found', 'Rule not found.')
    const b = (await request.json()) as Partial<Pick<RecordingRule, 'match' | 'channel_id' | 'new_only' | 'keep' | 'enabled'>>
    const rematch = (b.match !== undefined && plain(b.match) !== plain(rule.match)) || (b.enabled !== undefined && b.enabled !== rule.enabled)
    Object.assign(rule, b)
    // Disabling a rule, or changing what it matches, drops the upcoming recordings it scheduled.
    if (rematch) { dropUpcoming(s, rule.id); if (rule.enabled) applyRule(s, rule) }
    save(s)
    return HttpResponse.json(rule)
  }),
  http.delete(`${B}/recording-rules/:id`, ({ params, request }) => {
    const refused = refuse(request)
    if (refused) return refused
    const s = load()
    dropUpcoming(s, String(params.id))
    s.rules = s.rules.filter((r) => r.id !== params.id)
    save(s)
    return new HttpResponse(null, { status: 204 })
  }),
  // Playing a finished recording: rec:<id>. Anything else is the general resolve handler's business.
  http.post(`${B}/playback/resolve`, async ({ request }) => {
    const b = (await request.clone().json()) as { id?: string }
    if (!b.id?.startsWith('rec:')) return undefined
    const r = load().recordings.find((x) => x.id === b.id!.slice(4))
    if (!r) return error(404, 'not_found', 'This recording no longer exists.')
    if (r.state !== 'done') return error(409, 'recording_unavailable', 'This recording has no file to play.')
    const d: PlaybackDescriptor = {
      id: b.id, mode: 'direct', url: '/mock/sample.webm', mime: 'video/webm', live: false, buffer_available: false, headers: {},
      title: r.title, subtitle: [r.subtitle, r.channel_name].filter(Boolean).join(' · ') || null, poster: null, resume_position_ms: 0, duration_ms: 30_000,
    }
    return HttpResponse.json(d)
  }),
]
