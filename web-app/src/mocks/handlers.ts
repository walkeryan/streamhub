import type { components } from '../api/schema'
import { delay, http, HttpResponse } from 'msw'
import type { Channel, PlaybackDescriptor, Source, Tile } from '../api/types'
import {
  DISCLAIMER, EPISODE_NAMES, adultShelf, channelTile, channels, episodesOf, fav, groupLevels, homeRows, profiles, programmesFor, progress, seasonsOf, seedFor, shelfTiles, shelvesOf, showSeed,
  searchBroadcasts, sources, tileOf, titles, unavailable, watch, withNowNext,
} from './fixtures'
import { viewingHandlers } from './viewing'
import { annotate, requestHandlers, requestManagerConfigured, sourceSynced } from './requests'
import { roomHandlers } from './rooms'
import { recordingHandlers, withRecordingState, withRecordingStates } from './recordings'
import { adultUnlocked, holds, kindOfBody, mockProfile, ownProfile, peopleHandlers, secretRefused, setProfilePin } from './people'
import { feedAlternates, sportsChannels, sportsEvents, sportsNetworks } from './sports'

const B = '/api/v1'
const lineupKey = (id: string) => `mock.lineup.${id}`
function lineup(profile: string): { order: string[]; hidden: string[] } {
  const raw = sessionStorage.getItem(lineupKey(profile))
  return raw ? JSON.parse(raw) : { order: channels.map(c => c.id), hidden: [] }
}
const editionChoices = new Map<string, { variant: string; track: number | null }>()
const hiddenContinue = new Set<string>()
const accepted = new Set<string>(JSON.parse(sessionStorage.getItem('mock.accepted') ?? '[]') as string[])
const srcs: Source[] = [...sources]
let settings = { tmdb_api_key_set: false, region: 'US', sleep_timer_min: null as number | null, epg_refresh_hours: 12, transcode_hw: 'none', proxy_mode: 'auto', home_public_address: false }
// The shelves Home shows after New Releases, in order; empty = Home chooses. Kept for the tab, like the other fixtures a spec changes.
const HOME_SHELVES = 'mock.home_shelves'
const homeShelves = (): string[] => { try { return JSON.parse(sessionStorage.getItem(HOME_SHELVES) ?? '[]') as string[] } catch { return [] } }
/** Does an entry (Name, movies:Name, shows:Name) name a shelf the fixtures have? */
const knownShelf = (entry: string): boolean => {
  const sided = /^(movies|shows):(.+)$/.exec(entry)
  return (sided ? [sided[1] as 'movies' | 'shows'] : (['movies', 'shows'] as const)).some((section) => shelvesOf(section).some((s) => s.name === (sided ? sided[2] : entry)))
}
/** GET /settings: the two home_shelves fields are null for a caller without system.manage. */
const settingsFor = (request: Request) => {
  const manages = holds(request, 'system.manage')
  return {
    ...settings, request_manager_configured: requestManagerConfigured(), home_shelves: manages ? homeShelves() : null, home_shelves_unknown: manages ? homeShelves().filter((e) => !knownShelf(e)) : null,
    // The household's address in the mock is a documentation one.
    home_public_address: manages ? settings.home_public_address : null, home_public_address_seen: manages && settings.home_public_address ? '198.51.100.23' : null,
  }
}
let pinPolls = 0
const hiddenGroups = new Set<string>()
interface GroupRow { id: string; name: string; parent: string | null; path: string }
/** The groups of the fixture channels as the server lists them: each top-level group, then its children. */
function groupRows(): GroupRow[] {
  const below = new Map<string, string[]>()
  for (const path of new Set(channels.map((c) => c.group))) {
    const { parent } = groupLevels(path)
    if (!below.has(parent ?? path)) below.set(parent ?? path, [])
    if (parent) below.get(parent)!.push(path)
  }
  return [...below].flatMap(([top, paths], i) => [
    { id: `g${i}`, name: top, parent: null, path: top },
    ...paths.map((path, j) => ({ id: `g${i}c${j}`, name: groupLevels(path).name, parent: top, path })),
  ])
}
const groupHidden = (path: string) => hiddenGroups.has(path) || hiddenGroups.has(groupLevels(path).parent ?? path)
/** The stored groups a `group` filter names: an id or a path, a parent with its children, or a child's bare name if only one has it. */
function groupPaths(group: string): Set<string> {
  const rows = groupRows()
  const named = rows.find((r) => r.id === group) ?? rows.find((r) => r.path === group)
  const bare = named ? [] : rows.filter((r) => r.parent && r.name === group)
  const row = named ?? (bare.length === 1 ? bare[0] : undefined)
  if (!row) return new Set()
  return new Set([row.path, ...(row.parent ? [] : rows.filter((r) => r.parent === row.name && !hiddenGroups.has(r.path)).map((r) => r.path))])
}

const withProfile = (id: string) => {
  const p = profiles.find((x) => x.id === id) ?? mockProfile(id) ?? profiles[0]!
  return { ...p, disclaimer_accepted_version: accepted.has(p.id) ? DISCLAIMER.version : null }
}
const tokens = (id: string) => ({ access_token: `mock-access-${id}`, refresh_token: `mock-refresh-${id}`, profile: withProfile(id) })
const currentProfileId = (req: Request) => req.headers.get('authorization')?.replace('Bearer mock-access-', '') ?? 'p1'
/**
 * An episode (tmdb:episode:<show>:<season>:<n>) as the server describes one for the player: its show,
 * numbers, the episodes either side (season 2 follows season 1), intro and credits markers, previews.
 * The sample clip is 30 s: the intro is at 1–6 s and the credits from 21 s, or from `mock.credits_at` (ms)
 * when a spec sets it. `mock.no_markers` leaves them out (Up next then comes in the last third).
 */
function episodeDescriptor(id: string): PlaybackDescriptor | null {
  const m = /^tmdb:episode:(\d+):(\d+):(\d+)$/.exec(id)
  if (!m) return null
  const [show, season, n] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const series = tileById(`tmdb:tv:${show}`)
  const eps = (s: number) => episodesOf(show, s)
  const here = eps(season)[n - 1]
  if (!series || !here) return null
  const name = (t: Tile) => t.title.replace(/^(?:\d+\.|E\d+ ·)\s*/, '')
  const upNext = (s: number, e: number) => {
    const t = eps(s)[e - 1]
    return t ? { id: t.id, title: name(t), subtitle: `S${s} · E${e}`, image: t.backdrop ?? null, duration_ms: 30_000 } : null
  }
  const count = eps(season).length
  const next = n < count ? upNext(season, n + 1) : season < 2 ? upNext(season + 1, 1) : null
  const previous = n > 1 ? upNext(season, n - 1) : season > 1 ? upNext(season - 1, eps(season - 1).length) : null
  const creditsAt = Number(sessionStorage.getItem('mock.credits_at') ?? 21_000)
  const markers = sessionStorage.getItem('mock.no_markers') ? [] : [
    { type: 'intro' as const, start_ms: 1_000, end_ms: 6_000 },
    { type: 'credits' as const, start_ms: creditsAt, end_ms: 30_000 },
  ]
  return {
    id, mode: 'direct', url: '/mock/sample.webm', mime: 'video/webm', live: false, headers: {}, buffer_available: false,
    resume_position_ms: 0, duration_ms: 30_000, title: name(here), subtitle: `S${String(season).padStart(2, '0')}E${String(n).padStart(2, '0')} · ${series.title}`,
    poster: here.backdrop ?? null, channel_context: null, handoff: null,
    media_info: { source_name: 'Library', container: 'mov', bitrate_kbps: 9_400, video: { codec: 'h264', width: 1920, height: 1080 }, audio: { codec: 'aac', channels: 2 }, delivery: 'direct', output_width: 1920, output_height: 1080 },
    markers, show: { id: series.id, title: series.title }, season_number: season, episode_number: n, next, previous,
    thumbnails: { url: `${B}/thumbnails/mock-${show}-${season}-${n}`, interval_ms: 2000 },
  }
}

const tileById = (id: string): Tile | undefined => {
  const seed = seedFor(id)
  if (seed) return tileOf(seed)
  const c = channels.find((x) => x.id === id)
  if (c) return channelTile(withNowNext(c))
  const sport = sportsChannels.find((x) => x.id === id)
  return sport && channelTile(sport)
}

const countryMatches = (value: string | null | undefined, country: string | null) =>
  !country || country === 'all' || (country === 'und' ? !value?.trim() : value?.trim().toUpperCase() === country)

/** The browse listing as the server orders it: sort, then the order (or the sort's natural direction). */
function browseList(section: string, request: Request): Tile[] {
  const u = new URL(request.url)
  const source = u.searchParams.get('source_id')
  const sort = u.searchParams.get('sort')
  const order = u.searchParams.get('order')
  const query = u.searchParams.get('q')?.toLowerCase()
  const kind = section === 'movies' || section === 'vod' ? 'movie' : 'show'
  const category = u.searchParams.get('category')
  // One shelf lists everything on it; the unfiltered grid stays the small, known title set.
  const adult = u.searchParams.get('include_adult') === 'true' && adultUnlocked(request)
    ? adultShelf(kind === 'movie' ? 'movies' : 'shows').filter(s => !category || s.name === category).flatMap(s => s.items)
    : []
  let list = [...(category ? shelfTiles(kind, category) : titles), ...adult].filter((t) => t.kind === kind && (!query || t.title.toLowerCase().includes(query)) && (!source || t.sources.some((s) => s.source_id === source)))
  if (sort === 'title') list = [...list].sort((a, b) => a.title.localeCompare(b.title))
  if (sort === 'release' || sort === 'year') list = [...list].sort((a, b) => (b.year ?? 0) - (a.year ?? 0))
  if (sort === 'rating') list = [...list].sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
  const natural = sort === 'title' ? 'asc' : 'desc'
  return order && order !== natural ? [...list].reverse() : list
}

export const handlers = [
  ...viewingHandlers(currentProfileId),
  // first: these also stand in for the checks the server makes on every request
  ...peopleHandlers,
  ...requestHandlers,
  // watch rooms (REST and the room socket)
  ...roomHandlers,
  // recordings, rules and playing a finished recording (before the general resolve handler below)
  ...recordingHandlers,
  http.get(`${B}/sports/events`, async ({ request }) => { await delay(60); return HttpResponse.json(sportsEvents(new URL(request.url).searchParams, withRecordingState)) }),
  http.get(`${B}/sports/channels`, ({ request }) => HttpResponse.json(sportsNetworks(new URL(request.url).searchParams, withRecordingState))),
  http.get(`${B}/sports/recordings`, ({ request }) => {
    const params = new URL(request.url).searchParams
    return HttpResponse.json({ items: [], total: 0, limit: Number(params.get('limit') ?? 40), offset: Number(params.get('offset') ?? 0), truncated: false } satisfies components['schemas']['SportsRecordingsPage'])
  }),
  http.get(`${B}/playback/alternates`, ({ request }) => HttpResponse.json(feedAlternates(new URL(request.url).searchParams.get('id') ?? ''))),
  http.get(`${B}/playback/rewind`, () => HttpResponse.json({ mode: 'unavailable', advertised_start: null, advertised_end: null, verified: false, recording_enabled: false, granularity_seconds: 60, reason: 'Fixture has no archive.' })),
  http.put(`${B}/playback/preference`, ({ request }) => {
    const query = new URL(request.url).searchParams
    const key = `${currentProfileId(request)}|${query.get('id')}`
    const variant = query.get('variant_id')
    if (variant) editionChoices.set(key, { variant, track: query.has('audio_track') ? Number(query.get('audio_track')) : null })
    else editionChoices.delete(key)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(`${B}/playback/options`, ({ request }) => {
    const id = new URL(request.url).searchParams.get('id') ?? ''
    // Explicit verified-audio fixtures, never a production inference from a name.
    if (id.startsWith('ch:') || id.startsWith('prog:')) {
      const variant = id.startsWith('ch:') ? id : 'ch:fixture:programme'
      const option = { variant_id: variant, source_id: variant.split(':')[1], source_name: 'Verified fixture', provider_label: id, kind: 'channel', audio_languages: ['eng'], default_audio_language: 'eng', audio_verified: true, audio_evidence: 'audio_track_metadata', preferred_audio_available: true }
      return HttpResponse.json({ id, preferred_languages: ['eng'], options: [option], automatic_variant_id: variant, selection_required: false })
    }
    const episode = /^tmdb:episode:(\d+):(\d+):\d+$/.exec(id)
    const tile = tileById(id) ?? (episode ? episodesOf(Number(episode[1]), Number(episode[2])).find((e) => e.id === id) : undefined)
    // Films carry a probed two-track file; series keep source-reported audio and list no tracks.
    const audio_tracks: components['schemas']['AudioTrack'][] = tile?.kind === 'movie' ? [
      { index: 0, language: 'eng', codec: 'ac3', channels: 6, title: null, default: true },
      { index: 1, language: 'fra', codec: 'aac', channels: 2, title: null, default: false },
    ] : []
    const options: components['schemas']['PlaybackOption'][] = (tile?.sources ?? []).filter((s) => s.playable_in_app).map((s) => ({ variant_id: `mock:${s.source_id}:${id}`, source_id: s.source_id, source_name: s.name, provider_label: tile?.title ?? id, kind: tile?.kind, audio_languages: audio_tracks.length ? ['eng', 'fra'] : ['eng'], default_audio_language: 'eng', audio_verified: true, audio_evidence: audio_tracks.length ? 'media_probe' : 'audio_track_metadata', preferred_audio_available: true, audio_tracks, preferred_audio_track: audio_tracks.length ? 0 : null }))
    const remembered = editionChoices.get(`${currentProfileId(request)}|${id}`)
    const chosen = remembered ? options.find(o => o.variant_id === remembered.variant) : options[0]
    return HttpResponse.json({ id, preferred_languages: ['eng'], options, automatic_variant_id: chosen?.variant_id ?? null, selection_required: !!remembered && !chosen, remembered: !!remembered, remembered_audio_track: remembered?.track ?? null })
  }),
  http.get(`${B}/auth/config`, () => HttpResponse.json({ google_enabled: false, google_required: false })),
  http.get(`${B}/health`, () => HttpResponse.json({ ok: true })),
  http.get(`${B}/profiles/:id/prefs`, ({ params }) => HttpResponse.json(JSON.parse(sessionStorage.getItem(`mock.prefs.${params.id}`) ?? '{}'))),
  http.patch(`${B}/profiles/:id/prefs`, async ({ params, request }) => {
    const key = `mock.prefs.${params.id}`
    const previous = JSON.parse(sessionStorage.getItem(key) ?? '{}')
    const changes = await request.json() as Record<string, unknown>
    const onlyMissing = new URL(request.url).searchParams.get('only_missing') === 'true'
    const prefs = onlyMissing ? { ...changes, ...previous } : { ...previous, ...changes }
    sessionStorage.setItem(key, JSON.stringify(prefs))
    return HttpResponse.json(prefs)
  }),
  http.put(`${B}/profiles/:id/prefs`, async ({ params, request }) => {
    const document = await request.json()
    sessionStorage.setItem(`mock.prefs.${params.id}`, JSON.stringify(document))
    return HttpResponse.json(document)
  }),
  http.get(`${B}/browse/:section/genres`, () => HttpResponse.json([{ name: 'drama', count: 8 }, { name: 'comedy', count: 5 }])),
  http.get(`${B}/profiles`, ({ request }) => HttpResponse.json([...profiles, ...ownProfile(request)].map((p) => withProfile(p.id)))),
  http.post(`${B}/auth/login`, async ({ request }) => {
    const b = (await request.json()) as { profile_id: string; pin?: string | null; password?: string | null }
    const p = profiles.find((x) => x.id === b.profile_id)
    if (!p) return HttpResponse.json({ code: 'not_found', message: 'Unknown profile' }, { status: 404 })
    if (p.is_admin && b.password !== undefined && b.password !== null && b.password !== 'admin') return HttpResponse.json({ code: 'bad_credentials', message: 'Wrong password' }, { status: 401 })
    if (p.has_pin && b.pin !== '1234') return HttpResponse.json({ code: 'bad_pin', message: 'Wrong PIN' }, { status: 401 })
    return HttpResponse.json(tokens(p.id))
  }),
  http.post(`${B}/auth/refresh`, () => HttpResponse.json(tokens('p1'))),
  // What asked for a TV code, for the approval screen. "mock.pair.old" stands for a server that cannot say.
  http.post(`${B}/auth/device/lookup`, async ({ request }) => {
    if (sessionStorage.getItem('mock.pair.old')) return HttpResponse.json({ code: 'not_found', message: 'no such endpoint' }, { status: 404 })
    const b = (await request.json()) as { user_code?: string }
    if (!/^[A-Z0-9]{6}$/.test(b.user_code ?? '') || b.user_code === 'BADBAD') return HttpResponse.json({ code: 'not_found', message: 'unknown code' }, { status: 404 })
    return HttpResponse.json({ user_code: b.user_code, ip: '203.0.113.24', user_agent: 'StreamHub TV/1.0 (Android 12)', expires_in: 540 })
  }),
  http.post(`${B}/auth/device/approve`, async ({ request }) => {
    const b = (await request.json()) as { user_code?: string }
    if (!/^[A-Z0-9]{6}$/.test(b.user_code ?? '') || b.user_code === 'BADBAD') return HttpResponse.json({ code: 'invalid_code', message: 'That code is not valid or has expired.' }, { status: 404 })
    return new HttpResponse(null, { status: 204 })
  }),
  http.patch(`${B}/profiles/:id`, async ({ params, request }) => {
    const p = profiles.find((x) => x.id === params.id)
    if (p) Object.assign(p, await request.json())
    return HttpResponse.json(p ?? {})
  }),
  http.post(`${B}/profiles`, async ({ request }) => {
    const b = (await request.json()) as Partial<(typeof profiles)[number]>
    profiles.push({ id: `p${profiles.length + 1}`, name: b.name ?? 'New', avatar: null, is_admin: false, theme_id: 'midnight', languages: b.languages ?? ['eng'], has_pin: false, kids: !!b.kids, disclaimer_accepted_version: null })
    return HttpResponse.json({}, { status: 201 })
  }),
  http.delete(`${B}/profiles/:id`, ({ params }) => {
    const i = profiles.findIndex((x) => x.id === params.id)
    if (i > 0) profiles.splice(i, 1)
    return new HttpResponse(null, { status: 204 })
  }),
  http.put(`${B}/profiles/:id/pin`, async ({ params, request }) => {
    const b = (await request.json()) as { pin?: string | null; kind?: string; arrows?: boolean }
    const kind = kindOfBody(b)
    const refused = secretRefused(b.pin, kind)
    if (refused) return refused
    const p = profiles.find((x) => x.id === params.id)
    if (p) { p.has_pin = !!b.pin; p.pin_kind = b.pin ? kind : null; p.pin_arrows = b.pin && kind === 'arrows' ? b.pin.length : null }
    setProfilePin(String(params.id), b.pin ?? null, kind)
    return new HttpResponse(null, { status: 204 })
  }),
  // A test sets localStorage 'mock.tv-release' = 'none' for a server that publishes no TV build.
  http.get(`${B}/app/tv`, () => localStorage.getItem('mock.tv-release') === 'none' ? HttpResponse.json({ code: 'not_found', message: 'no TV app release is published here' }, { status: 404 }) : HttpResponse.json({ version_code: 8, version_name: '1.2.1', apk_url: '/downloads/streamhub-tv-1.2.1-8-debug.apk', sha256: 'a'.repeat(64) })),
  http.get(`${B}/disclaimer`, () => HttpResponse.json(DISCLAIMER)),
  http.post(`${B}/disclaimer/accept`, ({ request }) => {
    accepted.add(currentProfileId(request))
    sessionStorage.setItem('mock.accepted', JSON.stringify([...accepted]))
    return new HttpResponse(null, { status: 204 })
  }),

  http.get(`${B}/sources`, async () => { await delay(60); return HttpResponse.json(srcs) }),
  http.post(`${B}/sources`, async ({ request }) => {
    const b = (await request.json()) as { type: Source['type']; name: string; service_key?: string | null; config?: Record<string, unknown> }
    if ((b.type === 'xtream' || b.type === 'm3u') && !accepted.has(currentProfileId(request))) return HttpResponse.json({ code: 'disclaimer_required', message: 'Accept the disclaimer first.' }, { status: 412 })
    const s: Source = { id: `s_${Math.random().toString(36).slice(2, 7)}`, type: b.type, name: b.name, enabled: true, priority: srcs.length + 1, region: 'US', status: 'ok', last_sync: null, counts: {}, service_key: b.service_key ?? null, config_summary: {} }
    srcs.push(s)
    return HttpResponse.json(s, { status: 201 })
  }),
  http.delete(`${B}/sources/:id`, ({ params }) => {
    const i = srcs.findIndex((s) => s.id === params.id)
    if (i >= 0) srcs.splice(i, 1)
    return new HttpResponse(null, { status: 204 })
  }),
  http.post(`${B}/sources/:id/sync`, () => { sourceSynced(); return new HttpResponse(null, { status: 202 }) }),
  http.post(`${B}/sources/:id/test`, () => HttpResponse.json({ ok: true, message: 'Connection works.' })),
  http.post(`${B}/sources/plex/pin`, () => { pinPolls = 0; return HttpResponse.json({ pin_id: 4242, code: 'K7QP', link_url: 'https://plex.tv/link' }) }),
  http.get(`${B}/sources/plex/pin/:id`, () => {
    if (++pinPolls < 3) return new HttpResponse(null, { status: 428 })
    const s: Source = { id: 's_plex2', type: 'plex', name: 'Linked Plex', enabled: true, priority: 9, region: 'US', status: 'syncing', last_sync: null, counts: {}, service_key: null, config_summary: {} }
    if (!srcs.some((x) => x.id === s.id)) srcs.push(s)
    return HttpResponse.json([s])
  }),
  http.get(`${B}/services/registry`, () => HttpResponse.json([
    { key: 'netflix', name: 'Netflix', web_url: 'https://www.netflix.com/', supports_title_link: true },
    { key: 'hulu', name: 'Hulu', web_url: 'https://www.hulu.com/' },
    { key: 'disney', name: 'Disney+', web_url: 'https://www.disneyplus.com/' },
  ])),

  http.put(`${B}/history/continue-watching`, ({ request }) => {
    const query = new URL(request.url).searchParams
    const key = `${currentProfileId(request)}|${query.get('id')}`
    if (query.get('hidden') === 'true') hiddenContinue.add(key)
    else hiddenContinue.delete(key)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(`${B}/home`, async ({ request }) => {
    await delay(120)
    const home = homeRows(homeShelves())
    for (const row of home.rows) if (row.id === 'continue_watching') row.tiles = row.tiles.filter(t => !hiddenContinue.has(`${currentProfileId(request)}|${t.id}`))
    return HttpResponse.json(home)
  }),
  http.get(`${B}/search`, async ({ request }) => {
    const params = new URL(request.url).searchParams
    const q = (params.get('q') ?? '').trim().toLowerCase()
    const expand = new Set((params.get('expand') ?? '').split(','))
    // Like the server: discover=false is what the hub holds and answers at once; with discovery the
    // metadata provider is asked too, which takes a moment and adds the titles no source has.
    const discover = params.get('discover') !== 'false'
    const kinds = params.get('kinds')?.split(',')
    await delay(discover ? 350 : 100)
    const none = { results: [], play_here: [], open_in_app: [], channels: [], programmes: [], discovered: [], metadata_available: true, attribution: 'Availability data by JustWatch' }
    const section = (count: number, shown: number) => ({ count, more: count > shown })
    // Like the server: nothing is looked up for a letter.
    if (q.length < 2) return HttpResponse.json({ ...none, sections: { titles: section(0, 0), channels: section(0, 0), programmes: section(0, 0) }, order: ['programmes', 'channels', 'titles'] })
    const cap = (name: string, capped: number) => (expand.has(name) ? 100 : capped)
    // Titles: the library first, then what only the metadata provider knows, each by where the query falls in the name.
    const rank = (name: string) => { const at = name.toLowerCase().indexOf(q); return name.toLowerCase() === q ? 0 : at === 0 ? 1 : at > 0 && name[at - 1] === ' ' ? 2 : 3 }
    const hits = [...titles, ...(discover ? unavailable : [])].filter((t) => t.title.toLowerCase().includes(q))
      .sort((a, b) => rank(a.title) - rank(b.title) || Number(!a.sources.length) - Number(!b.sources.length))
    const exact = (c: Channel) => c.name.toLowerCase() === q || String(c.number) === q
    const live = !kinds || kinds.includes('channel')
    const chans = !live ? [] : channels.filter((c) => c.name.toLowerCase().includes(q) || String(c.number) === q)
      .sort((a, b) => Number(exact(b)) - Number(exact(a)) || a.number - b.number)
    const broadcasts = !kinds || kinds.includes('programme') ? searchBroadcasts(q) : []
    const shown = { titles: hits.slice(0, cap('titles', 40)).map(annotate), channels: chans.slice(0, cap('channels', 12)), programmes: broadcasts.slice(0, cap('programmes', 24)) }
    return HttpResponse.json({
      results: shown.titles,
      actors: discover && 'casey vale'.includes(q) ? [{ id: '72', name: 'Casey Vale' }] : [],
      services: 'netflix'.includes(q) ? [{ id: 'netflix', name: 'Netflix' }] : [],
      play_here: shown.titles.filter((t) => t.sources.some((s) => s.playable_in_app)),
      open_in_app: shown.titles.filter((t) => t.sources.length > 0 && !t.sources.some((s) => s.playable_in_app)),
      channels: shown.channels.map(withNowNext), programmes: shown.programmes.map(withRecordingState),
      discovered: shown.titles.filter((t) => unavailable.some((u) => u.id === t.id)).map((t) => t.id), metadata_available: true,
      sections: { titles: section(hits.length, shown.titles.length), channels: section(chans.length, shown.channels.length), programmes: section(broadcasts.length, shown.programmes.length) },
      order: chans[0] && exact(chans[0]) ? ['channels', 'programmes', 'titles'] : ['programmes', 'channels', 'titles'],
      attribution: 'Availability data by JustWatch',
    })
  }),
  http.get(`${B}/titles/:id`, async ({ params }) => {
    const id = decodeURIComponent(String(params.id))
    await delay(80)
    // Seasons and episodes of a fixture series, each naming its show and season as the server does.
    if (id.startsWith('tmdb:season:') || id.startsWith('tmdb:episode:')) {
      const [, kind, show, season, number] = id.split(':')
      const seed = showSeed(Number(show))
      const eps = episodesOf(Number(show), Number(season))
      if (!seed || !eps.length) return HttpResponse.json({ code: 'not_found', message: 'Title not found' }, { status: 404 })
      const parent = {
        show: { id: `tmdb:tv:${seed.n}`, title: seed.title, backdrop: tileOf(seed).backdrop },
        season: { id: `tmdb:season:${show}:${season}`, title: `Season ${season}`, number: Number(season) },
        episode_number: kind === 'episode' ? Number(number) : null, episode_count: kind === 'episode' ? eps.length : null,
        episode_title: kind === 'episode' ? EPISODE_NAMES[Number(number) - 1] ?? null : null,
      }
      const common = { attribution: 'Availability data by JustWatch', content_rating: 'TV-14', parent, cast: [], seasons: [], genres: seed.genres }
      if (kind === 'season') return HttpResponse.json({ ...eps[0]!, ...common, id, kind: 'season', title: `Season ${season}`, episodes: eps, progress_pct: null, overview: seed.overview, runtime_min: null })
      const ep = eps.find((e) => e.id === id)
      if (!ep) return HttpResponse.json({ code: 'not_found', message: 'Title not found' }, { status: 404 })
      return HttpResponse.json({
        ...ep, ...common, episodes: [], runtime_min: ep.runtime_min, rating: seed.rating, release_date: `${seed.year}-0${Number(season)}-${String(10 + Number(number)).padStart(2, '0')}`,
        overview: `${EPISODE_NAMES[Number(number) - 1]}: ${seed.overview}`,
      })
    }
    const seed = seedFor(id)
    const t = tileById(id)
    if (t?.kind === 'channel') return HttpResponse.json({ ...t, attribution: 'Provider guide metadata' })
    if (!seed || !t) return HttpResponse.json({ code: 'not_found', message: 'Title not found' }, { status: 404 })
    const a = annotate(t)
    // Seasons and episodes come from a library copy; a title no source has only offers seasons to request.
    const show = seed.kind === 'show' && a.sources.length > 0
    return HttpResponse.json({
      ...a, overview: seed.overview, genres: seed.genres, runtime_min: seed.runtime,
      cast: ['Mara Ellison', 'Theo Okafor', 'Junko Hale', 'Luis Ferreira', 'Ines Varga', 'Kofi Mensah'].map((name, i) => ({ name, character: ['Lead', 'Rival', 'Mentor', 'Stranger', 'Harbor Master', 'Ferryman'][i] ?? '', photo: null })),
      seasons: show ? seasonsOf(seed.n) : [], episodes: show ? episodesOf(seed.n, 1) : [],
      content_rating: seed.kind === 'show' ? 'TV-14' : 'PG-13', release_date: `${seed.year}-03-14`,
      request_seasons: seed.kind === 'show' && a.requestable ? [1, 2, 3] : [],
      attribution: 'Availability data by JustWatch',
    })
  }),
  // The household's activity: invented entries for a few fixture titles. A kids profile sees the kids' only.
  http.get(`${B}/titles/:id/activity`, async ({ params, request }) => {
    const id = decodeURIComponent(String(params.id))
    await delay(60)
    const me = profiles.find((p) => p.id === currentProfileId(request))
    const hours = (n: number) => new Date(Date.now() - n * 3_600_000).toISOString()
    const all: Array<components['schemas']['TitleActivityEntry'] & { kids: boolean }> = id === 'tmdb:tv:201' ? [
      { profile_id: 'p1', name: 'Alex', me: false, action: 'watching', at: hours(2), season_number: 1, episode_number: 2, progress_pct: 31, minutes_left: 33, season_finished: false, kids: false },
      { profile_id: 'p2', name: 'Guest', me: false, action: 'finished', at: hours(72), season_number: 1, episode_number: 6, season_finished: true, kids: false },
      { profile_id: 'p3', name: 'Kids', me: false, action: 'listed', at: hours(24 * 8), season_finished: false, kids: true },
    ] : id === 'tmdb:movie:103' ? [
      { profile_id: 'p2', name: 'Guest', me: false, action: 'finished', at: hours(30), season_finished: false, kids: false },
    ] : []
    const entries = all
      .filter((e) => !me?.kids || e.kids || e.profile_id === me.id)
      .map(({ kids: _kids, ...e }) => ({ ...e, me: e.profile_id === me?.id }))
    return HttpResponse.json({ id, entries })
  }),
  // More like this: other fixture titles of the same kind; what plays here carries its in-app badge.
  http.get(`${B}/titles/:id/similar`, async ({ params }) => {
    const id = decodeURIComponent(String(params.id))
    await delay(120)
    const kind = id.startsWith('tmdb:tv:') ? 'show' : id.startsWith('tmdb:movie:') ? 'movie' : null
    const tiles = kind ? [...titles, ...unavailable].filter((t) => t.kind === kind && t.id !== id).slice(0, 10) : []
    return HttpResponse.json({ id, tiles })
  }),
  // Fixture metadata catalog; source/language scope changes availability only.
  // Audio evidence here is the explicit English fixture used by playback/options.
  http.get(`${B}/discover/:section`, async ({ params, request }) => {
    const u = new URL(request.url)
    const page = Math.max(1, Math.min(500, Number(u.searchParams.get('page') ?? 1)))
    const source = u.searchParams.get('source_id'), language = u.searchParams.get('language')
    const year = u.searchParams.get('year'), sort = u.searchParams.get('sort')
    const kind = params.section === 'shows' ? 'show' : 'movie'
    const profile = profiles.find((p) => p.id === currentProfileId(request))
    const allowedAudio = !language || language === 'all' || language.split(',').includes('eng')
    let list = titles.filter((t) => t.kind === kind && (!year || t.year === Number(year)))
    if (u.searchParams.has('actor')) list = list.filter(t => u.searchParams.get('actor') === '72' && t.id === 'tmdb:movie:101')
    if (u.searchParams.has('service')) list = list.filter(t => u.searchParams.get('service') === 'netflix' && t.sources.some(s => s.source_id === 's_nf'))
    if (sort === 'rating') list = [...list].sort((a,b) => (b.rating ?? 0) - (a.rating ?? 0))
    if (sort === 'newest') list = [...list].sort((a,b) => (b.year ?? 0) - (a.year ?? 0))
    // Unclassified fixtures are not a parental-policy bypass.
    if (profile?.content_restricted) list = []
    const size = 20
    await delay(60)
    return HttpResponse.json({
      tiles: list.slice((page-1)*size,page*size).map((tile) => ({ ...tile, sources: allowedAudio ? tile.sources.filter((s) => !source || s.source_id === source) : [] })),
      page, page_size:size, total:profile?.content_restricted ? null : list.length,
      has_more:page*size<list.length, coverage_note:'Synthetic metadata fixture catalog. Availability is checked separately; this is not exhaustive coverage.',
    } satisfies components['schemas']['DiscoveryPage'])
  }),
  http.get(`${B}/browse/:section/shelves`, async ({ params, request }) => {
    const u = new URL(request.url)
    const section = params.section === 'movies' || params.section === 'vod' ? 'movies' : 'shows'
    const rows = Number(u.searchParams.get('limit_rows') ?? 12), items = Number(u.searchParams.get('limit_items') ?? 20)
    await delay(80)
    // Adult titles are listed only when asked for, and only on a session that unlocked them.
    const all = u.searchParams.get('include_adult') === 'true' && adultUnlocked(request) ? [...shelvesOf(section), ...adultShelf(section)] : shelvesOf(section)
    return HttpResponse.json({ shelves: all.slice(0, rows).map((s) => ({ ...s, items: s.items.slice(0, items) })) } satisfies components['schemas']['ShelfList'])
  }),
  http.get(`${B}/browse/:section/categories`, () => HttpResponse.json([])),
  http.get(`${B}/browse/:section/languages`, () => HttpResponse.json([{ code: 'eng', name: 'English', count: titles.length }])),
  http.get(`${B}/browse/:section/jumps`, ({ params, request }) => {
    const u = new URL(request.url)
    const sort = u.searchParams.get('sort') ?? 'added'
    const list = browseList(String(params.section), request)
    const label = (t: Tile) => sort === 'title' ? (/^[a-z]/i.test(t.title) ? t.title[0]!.toUpperCase() : '#')
      : (sort === 'year' || sort === 'release') ? (t.year ? String(t.year) : 'Unknown') : sort === 'rating' ? (t.rating != null ? `${Math.floor(t.rating)}+` : 'Unrated') : 'Recently'
    const jumps: Array<{ label: string; offset: number }> = []
    list.forEach((t, offset) => { const l = label(t); if (!jumps.some((j) => j.label === l)) jumps.push({ label: l, offset }) })
    return HttpResponse.json({ sort, page_size: 60, jumps })
  }),
  http.get(`${B}/browse/:section`, async ({ params, request }) => {
    const u = new URL(request.url)
    const page = Number(u.searchParams.get('page') ?? 1)
    const list = browseList(String(params.section), request)
    const size = 60
    await delay(80)
    return HttpResponse.json({ tiles: list.slice((page - 1) * size, page * size), page, page_size: size, has_more: page * size < list.length, total: list.length })
  }),

  http.get(`${B}/channels/entry`, ({ request }) => {
    const query = new URL(request.url).searchParams
    const saved = JSON.parse(sessionStorage.getItem('mock.live.entries') ?? '{}') as Record<string, components['schemas']['LiveProgrammeEntry']>
    return HttpResponse.json(saved[`${query.get('channel_id')}|${query.get('start')}`] ?? {})
  }),
  http.get(`${B}/channels/lineup/hidden`, ({ request }) => HttpResponse.json(channels.filter(c => lineup(currentProfileId(request)).hidden.includes(c.id)).map(withNowNext))),
  http.post(`${B}/channels/lineup`, async ({ request }) => {
    const pid = currentProfileId(request), value = lineup(pid)
    const { channel_id: id, action } = await request.json() as { channel_id: string; action: string }
    const index = value.order.indexOf(id)
    if (index < 0) return new HttpResponse(null, { status: 404 })
    if (action === 'hide') value.hidden = [...new Set([...value.hidden, id])]
    else if (action === 'show') value.hidden = value.hidden.filter(c => c !== id)
    else {
      value.order.splice(index, 1)
      value.order.splice(action === 'top' ? 0 : Math.max(0, index + (action === 'up' ? -1 : 1)), 0, id)
    }
    sessionStorage.setItem(lineupKey(pid), JSON.stringify(value))
    return new HttpResponse(null, { status: 204 })
  }),
  http.delete(`${B}/channels/lineup`, ({ request }) => { sessionStorage.removeItem(lineupKey(currentProfileId(request))); return new HttpResponse(null, { status: 204 }) }),
  http.get(`${B}/channels`, async ({ request }) => {
    const u = new URL(request.url)
    const group = u.searchParams.get('group')
    const country = u.searchParams.get('country')
    const lang = u.searchParams.get('language')
    const favs = u.searchParams.get('favorites') === 'true'
    const q = u.searchParams.get('q')?.toLowerCase()
    const pid = currentProfileId(request)
    const langs = lang === 'all' ? [] : lang ? lang.split(',') : profiles.find((p) => p.id === pid)?.languages ?? []
    const selected = group ? groupPaths(group) : null
    const personal = lineup(pid)
    const list = personal.order.flatMap(id => channels.find(c => c.id === id) ?? []).filter(c => !personal.hidden.includes(c.id))
      .filter((c) => countryMatches(c.country, country) && !groupHidden(c.group) && (!selected || selected.has(c.group)) && (!langs.length || langs.includes(c.language)) && (!favs || fav.has(c.id)) && (!q || c.name.toLowerCase().includes(q) || String(c.number) === q))

    const offset = Number(u.searchParams.get('offset') ?? 0), limit = Number(u.searchParams.get('limit') ?? list.length)
    return HttpResponse.json({ list_id: `list:${group ?? 'all'}:${lang ?? 'p'}:${favs}`, channels: list.slice(offset, offset + limit).map(withNowNext).map(withRecordingStates), total: list.length, limit, offset })
  }),
  http.get(`${B}/channels/groups`, ({ request }) => {
    const u = new URL(request.url), lang = u.searchParams.get('language'), country = u.searchParams.get('country')
    const langs = lang === 'all' ? [] : lang ? lang.split(',') : profiles.find((p) => p.id === currentProfileId(request))?.languages ?? []
    const q = u.searchParams.get('q')?.toLowerCase()
    // One pass over the channels: a large fixture catalog has hundreds of groups.
    const counts = new Map<string, number>()
    for (const c of channels) {
      if (countryMatches(c.country, country) && (!langs.length || langs.includes(c.language)) && (!q || c.name.toLowerCase().includes(q) || String(c.number) === q)) {
        counts.set(c.group, (counts.get(c.group) ?? 0) + 1)
        // A parent counts its children's channels with its own.
        const { parent } = groupLevels(c.group)
        if (parent) counts.set(parent, (counts.get(parent) ?? 0) + 1)
      }
    }
    return HttpResponse.json(groupRows().map((g) => ({ id: g.id, source_id: 's_xt', name: g.name, parent: g.parent, path: g.path, channel_count: counts.get(g.path) ?? 0,
      hidden: groupHidden(g.path), language_override: null })).filter((g) => g.channel_count > 0))
  }),
  http.patch(`${B}/channels/groups/:id`, async ({ params, request }) => {
    const b = (await request.json()) as { hidden?: boolean }
    const path = groupRows().find((g) => g.id === params.id)?.path
    if (path && b.hidden !== undefined) { if (b.hidden) hiddenGroups.add(path); else hiddenGroups.delete(path) }
    return HttpResponse.json({})
  }),
  http.get(`${B}/channels/languages`, () => {
    const names: Record<string, string> = { eng: 'English', spa: 'Spanish', fra: 'French', deu: 'German', hin: 'Hindi' }
    const counts = new Map<string, number>()
    channels.forEach((c) => counts.set(c.language, (counts.get(c.language) ?? 0) + 1))
    return HttpResponse.json([...counts].map(([code, count]) => ({ code, name: names[code] ?? code, count })))
  }),
  http.get(`${B}/epg`, ({ request }) => {
    const u = new URL(request.url)
    const ids = (u.searchParams.get('channel_ids') ?? '').split(',').filter(Boolean)
    const from = Date.parse(u.searchParams.get('from') ?? '') || Date.now() - 3600_000
    const to = Date.parse(u.searchParams.get('to') ?? '') || Date.now() + 6 * 3600_000
    return HttpResponse.json(Object.fromEntries(ids.map((id) => [id, programmesFor(channels.find((c) => c.id === id) ?? channels[0]!, from, to).map(withRecordingState)])))
  }),

  http.get(`${B}/playback/generation`, () => HttpResponse.json({ generation: 'fixture-generation' })),
  // Scrub previews: a made-up frame naming the position it was asked for.
  http.get(`${B}/thumbnails/:token`, ({ request }) => {
    const ms = Number(new URL(request.url).searchParams.get('ms') ?? 0)
    const s = Math.floor(ms / 1000), hue = (s * 37) % 360
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 55% 38%)"/><stop offset="1" stop-color="hsl(${(hue + 70) % 360} 60% 14%)"/></linearGradient></defs><rect width="320" height="180" fill="url(#g)"/><circle cx="${40 + (s * 23) % 240}" cy="70" r="34" fill="#fff" opacity=".18"/><text x="18" y="160" font-family="Inter,Arial" font-size="22" font-weight="700" fill="#fff" opacity=".85">Frame ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}</text></svg>`
    return new HttpResponse(svg, { headers: { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'max-age=3600' } })
  }),
  http.post(`${B}/playback/resolve`, async ({ request }) => {
    const b = (await request.json()) as { id: string; source_id?: string | null; start_position_ms?: number | null; max_height?: number | null }
    const ep = episodeDescriptor(b.id)
    if (ep) return HttpResponse.json(ep)
    const t = tileById(b.id)
    const handoffOnly = t && t.kind !== 'channel' && !t.sources.some((s) => s.playable_in_app)
    const chan = b.id.startsWith('ch:')
    const base = { id: b.id, buffer_available: false, headers: {}, title: t?.title ?? b.id, subtitle: t?.subtitle ?? null, poster: t?.poster ?? null }
    if (handoffOnly || b.source_id === 's_nf') {
      const d: PlaybackDescriptor = {
        ...base, mode: 'handoff', url: null, live: false, resume_position_ms: 0,
        handoff: { service_key: 'netflix', android_package: 'com.netflix.ninja', android_uri: null, android_extras: {}, web_url: 'https://www.netflix.com/', supports_title_link: false },
      }
      return HttpResponse.json(d)
    }
    // A film only the provider has is converted for a browser: a restart-at-offset stream whose media time zero
    // is where it was asked to start (on the server's 5 s grid) and which knows only its own clip beyond that.
    // A quality cap below the file's height does the same to a film the browser would have played as it is.
    const converted = !!t && !chan && t.sources.filter((s) => s.playable_in_app).every((s) => s.source_type === 'xtream')
    const file = converted
      ? { source_name: 'IPTV', container: 'matroska', bitrate_kbps: 18_250, video: { codec: 'hevc', width: 3840, height: 1600, frame_rate: 23.976, dynamic_range: 'hdr10' as const }, audio: { codec: 'eac3', channels: 6, language: 'eng' } }
      : { source_name: t?.sources.find((s) => s.playable_in_app)?.name ?? null, container: 'mov', bitrate_kbps: 9_400, video: { codec: 'h264', width: 1920, height: 1080 }, audio: { codec: 'aac', channels: 2 } }
    const cap = !chan && (b.max_height === 480 || b.max_height === 720 || b.max_height === 1080) && b.max_height < file.video.height ? b.max_height : null
    if (converted || cap) {
      const start = Math.floor((b.start_position_ms ?? 0) / 5000) * 5000
      // The server's sizes: at most 1920 wide, then down to the cap, the shape kept and both sides even.
      const wide = Math.min(1920, file.video.width)
      const tall = Math.round((file.video.height * wide) / file.video.width / 2) * 2
      const [width, height] = cap && tall > cap ? [Math.round((wide * cap) / tall / 2) * 2, cap] : [wide, tall]
      const d: PlaybackDescriptor = {
        ...base, mode: 'proxy', url: `/mock/room.webm?start=${start}${cap ? `&cap=${cap}` : ''}`, mime: 'video/webm', live: false,
        resume_position_ms: start, duration_ms: converted ? 112 * 60_000 : 30_000, start_position_ms: start,
        // As the server describes a film it transcodes for a browser; fields it does not know are absent.
        media_info: { ...file, delivery: 'transcode', encoder: 'hardware', output_width: width, output_height: height, ...(cap ? { max_height: cap } : {}) },
      }
      return HttpResponse.json(d)
    }
    const d: PlaybackDescriptor = {
      ...base, mode: 'direct', url: '/mock/sample.webm', mime: 'video/webm', live: chan,
      resume_position_ms: progress[b.id]?.pos ? Math.min(progress[b.id]!.pos, 20_000) : 0, duration_ms: chan ? null : 30_000,
      channel_context: chan ? { list_id: 'mock', index: channels.findIndex((c) => c.id === b.id), count: channels.length } : null,
      media_info: chan ? { source_name: 'IPTV', delivery: 'remux' } : { ...file, delivery: 'direct', output_width: 1920, output_height: 1080 },
      // A film's credits, when a spec asks for them (Skip credits then ends the film).
      markers: !chan && sessionStorage.getItem('mock.film_credits') ? [{ type: 'credits', start_ms: Number(sessionStorage.getItem('mock.film_credits')), end_ms: 30_000 }] : [],
    }
    return HttpResponse.json(d)
  }),
  // A stop at the end marks a title watched, a stop at the start unwatched, as the server reads them.
  http.post(`${B}/playback/progress`, async ({ request }) => {
    const b = (await request.json()) as { id: string; watched?: boolean; position_ms: number; duration_ms?: number | null; state: string }
    if (b.watched === true) progress[b.id] = { pos: 1, dur: 1 }
    else if (b.state === 'stopped' && b.position_ms <= 0) delete progress[b.id]
    else if (b.state === 'stopped' && b.duration_ms && b.position_ms >= b.duration_ms) progress[b.id] = { pos: b.duration_ms, dur: b.duration_ms }
    return new HttpResponse(null, { status: 204 })
  }),

  http.get(`${B}/favorites`, () => HttpResponse.json([
    ...channels.filter((c) => fav.has(c.id)).map(withNowNext).map(channelTile),
    ...titles.filter((t) => fav.has(t.id)).map((t) => ({ ...t, is_favorite: true })),
  ])),
  http.put(`${B}/favorites`, async ({ request }) => { fav.add(((await request.json()) as { id: string }).id); return new HttpResponse(null, { status: 204 }) }),
  http.delete(`${B}/favorites`, ({ request }) => { fav.delete(new URL(request.url).searchParams.get('id') ?? ''); return new HttpResponse(null, { status: 204 }) }),
  http.get(`${B}/watchlist`, () => HttpResponse.json(titles.filter((t) => watch.has(t.id)))),
  http.put(`${B}/watchlist`, async ({ request }) => { watch.add(((await request.json()) as { id: string }).id); return new HttpResponse(null, { status: 204 }) }),
  http.delete(`${B}/watchlist`, ({ request }) => { watch.delete(new URL(request.url).searchParams.get('id') ?? ''); return new HttpResponse(null, { status: 204 }) }),
  http.get(`${B}/history`, () => HttpResponse.json(Object.keys(progress).map(tileById).filter((t): t is Tile => !!t))),
  http.get(`${B}/themes`, () => HttpResponse.json([])),
  http.get(`${B}/settings`, ({ request }) => HttpResponse.json(settingsFor(request))),
  http.patch(`${B}/settings`, async ({ request }) => {
    const { home_shelves, ...rest } = (await request.json()) as { home_shelves?: string[] | null } & Record<string, unknown>
    if (home_shelves !== undefined) {
      if (!holds(request, 'system.manage')) return HttpResponse.json({ code: 'forbidden', message: 'Missing permission: system.manage', missing: ['system.manage'] }, { status: 403 })
      // Stored trimmed, without blanks or repeats.
      const kept = [...new Set((home_shelves ?? []).map((e) => e.trim()).filter(Boolean))]
      if (kept.length > 24 || kept.some((e) => e.length > 200)) return HttpResponse.json({ code: 'validation_error', message: 'home_shelves takes at most 24 entries of at most 200 characters.' }, { status: 422 })
      sessionStorage.setItem(HOME_SHELVES, JSON.stringify(kept))
    }
    settings = { ...settings, ...rest }
    return HttpResponse.json(settingsFor(request))
  }),
  http.put(`${B}/settings/tmdb_key`, () => { settings.tmdb_api_key_set = true; return new HttpResponse(null, { status: 204 }) }),
]
