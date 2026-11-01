import type { components } from '../api/schema'
import { delay, http, HttpResponse } from 'msw'
import type { PlaybackDescriptor, Source, Tile } from '../api/types'
import {
  DISCLAIMER, channelTile, channels, episodesOf, fav, homeRows, profiles, programmesFor, progress, seasonsOf, seedFor, sources, tileOf, titles,
  watch, withNowNext,
} from './fixtures'

const B = '/api/v1'
const accepted = new Set<string>(JSON.parse(sessionStorage.getItem('mock.accepted') ?? '[]') as string[])
const srcs: Source[] = [...sources]
let settings = { tmdb_api_key_set: false, region: 'US', sleep_timer_min: null as number | null, epg_refresh_hours: 12, transcode_hw: 'none', proxy_mode: 'auto' }
let pinPolls = 0
const hiddenGroups = new Set<string>()

const withProfile = (id: string) => {
  const p = profiles.find((x) => x.id === id) ?? profiles[0]!
  return { ...p, disclaimer_accepted_version: accepted.has(p.id) ? DISCLAIMER.version : null }
}
const tokens = (id: string) => ({ access_token: `mock-access-${id}`, refresh_token: `mock-refresh-${id}`, profile: withProfile(id) })
const currentProfileId = (req: Request) => req.headers.get('authorization')?.replace('Bearer mock-access-', '') ?? 'p1'
const tileById = (id: string): Tile | undefined => {
  const t = titles.find((x) => x.id === id)
  if (t) return tileOf(seedFor(id)!)
  const c = channels.find((x) => x.id === id)
  return c ? channelTile(withNowNext(c)) : undefined
}

const countryMatches = (value: string | null | undefined, country: string | null) =>
  !country || country === 'all' || (country === 'und' ? !value?.trim() : value?.trim().toUpperCase() === country)

export const handlers = [
  http.get(`${B}/sports/recordings`, ({ request }) => {
    const params = new URL(request.url).searchParams
    return HttpResponse.json({ items: [], total: 0, limit: Number(params.get('limit') ?? 40), offset: Number(params.get('offset') ?? 0), truncated: false } satisfies components['schemas']['SportsRecordingsPage'])
  }),
  http.get(`${B}/playback/rewind`, () => HttpResponse.json({ mode: 'unavailable', advertised_start: null, advertised_end: null, verified: false, recording_enabled: false, granularity_seconds: 60, reason: 'Fixture has no archive.' })),
  http.get(`${B}/playback/options`, ({ request }) => {
    const id = new URL(request.url).searchParams.get('id') ?? ''
    // Explicit verified-audio fixtures, never a production inference from a name.
    if (id.startsWith('ch:') || id.startsWith('prog:')) {
      const variant = id.startsWith('ch:') ? id : 'ch:fixture:programme'
      const option = { variant_id: variant, source_id: variant.split(':')[1], source_name: 'Verified fixture', provider_label: id, kind: 'channel', audio_languages: ['eng'], default_audio_language: 'eng', audio_verified: true, audio_evidence: 'audio_track_metadata', preferred_audio_available: true }
      return HttpResponse.json({ id, preferred_languages: ['eng'], options: [option], automatic_variant_id: variant, selection_required: false })
    }
    const tile = tileById(id)
    const options = (tile?.sources ?? []).filter((s) => s.playable_in_app).map((s) => ({ variant_id: `mock:${s.source_id}:${id}`, source_id: s.source_id, source_name: s.name, provider_label: tile?.title ?? id, kind: tile?.kind, audio_languages: ['eng'], default_audio_language: 'eng', audio_verified: true, audio_evidence: 'audio_track_metadata', preferred_audio_available: true }))
    return HttpResponse.json({ id, preferred_languages: ['eng'], options, automatic_variant_id: options[0]?.variant_id ?? null, selection_required: false })
  }),
  http.get(`${B}/auth/config`, () => HttpResponse.json({ google_enabled: false, google_required: false })),
  http.get(`${B}/health`, () => HttpResponse.json({ ok: true })),
  http.get(`${B}/profiles/:id/prefs`, ({ params }) => HttpResponse.json(JSON.parse(sessionStorage.getItem(`mock.prefs.${params.id}`) ?? '{}'))),
  http.put(`${B}/profiles/:id/prefs`, async ({ params, request }) => {
    const document = await request.json()
    sessionStorage.setItem(`mock.prefs.${params.id}`, JSON.stringify(document))
    return HttpResponse.json(document)
  }),
  http.get(`${B}/browse/:section/genres`, () => HttpResponse.json([{ name: 'drama', count: 8 }, { name: 'comedy', count: 5 }])),
  http.get(`${B}/profiles`, () => HttpResponse.json(profiles.map((p) => withProfile(p.id)))),
  http.post(`${B}/auth/login`, async ({ request }) => {
    const b = (await request.json()) as { profile_id: string; pin?: string | null; password?: string | null }
    const p = profiles.find((x) => x.id === b.profile_id)
    if (!p) return HttpResponse.json({ code: 'not_found', message: 'Unknown profile' }, { status: 404 })
    if (p.is_admin && b.password !== undefined && b.password !== null && b.password !== 'admin') return HttpResponse.json({ code: 'bad_credentials', message: 'Wrong password' }, { status: 401 })
    if (p.has_pin && b.pin !== '1234') return HttpResponse.json({ code: 'bad_pin', message: 'Wrong PIN' }, { status: 401 })
    return HttpResponse.json(tokens(p.id))
  }),
  http.post(`${B}/auth/refresh`, () => HttpResponse.json(tokens('p1'))),
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
    const b = (await request.json()) as { pin?: string | null }
    const p = profiles.find((x) => x.id === params.id)
    if (p) p.has_pin = !!b.pin
    return new HttpResponse(null, { status: 204 })
  }),
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
  http.post(`${B}/sources/:id/sync`, () => new HttpResponse(null, { status: 202 })),
  http.post(`${B}/sources/:id/test`, () => HttpResponse.json({ ok: true, message: 'Connection works.' })),
  http.post(`${B}/sources/plex/pin`, () => { pinPolls = 0; return HttpResponse.json({ pin_id: 4242, code: 'K7QP', link_url: 'https://app.plex.tv/auth#?code=K7QP' }) }),
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

  http.get(`${B}/home`, async () => { await delay(120); return HttpResponse.json(homeRows()) }),
  http.get(`${B}/search`, async ({ request }) => {
    const q = (new URL(request.url).searchParams.get('q') ?? '').toLowerCase()
    await delay(100)
    const hits = titles.filter((t) => t.title.toLowerCase().includes(q))
    const chans = channels.filter((c) => c.name.toLowerCase().includes(q)).map(withNowNext)
    return HttpResponse.json({
      play_here: hits.filter((t) => t.sources.some((s) => s.playable_in_app)),
      open_in_app: hits.filter((t) => !t.sources.some((s) => s.playable_in_app)),
      channels: chans, attribution: 'Availability data by JustWatch',
    })
  }),
  http.get(`${B}/titles/:id`, async ({ params }) => {
    const id = decodeURIComponent(String(params.id))
    await delay(80)
    if (id.startsWith('tmdb:season:')) {
      const [, , show, season] = id.split(':')
      const eps = episodesOf(Number(show), Number(season))
      return HttpResponse.json({ ...eps[0]!, id, kind: 'season', title: `Season ${season}`, episodes: eps, seasons: [], attribution: 'Availability data by JustWatch' })
    }
    const seed = seedFor(id)
    const t = tileById(id)
    if (t?.kind === 'channel') return HttpResponse.json({ ...t, attribution: 'Provider guide metadata' })
    if (!seed || !t) return HttpResponse.json({ code: 'not_found', message: 'Title not found' }, { status: 404 })
    const show = seed.kind === 'show'
    return HttpResponse.json({
      ...t, overview: seed.overview, genres: seed.genres, runtime_min: seed.runtime,
      cast: ['Mara Ellison', 'Theo Okafor', 'Junko Hale', 'Luis Ferreira'].map((name, i) => ({ name, character: ['Lead', 'Rival', 'Mentor', 'Stranger'][i] ?? '', photo: null })),
      seasons: show ? seasonsOf(seed.n) : [], episodes: show ? episodesOf(seed.n, 1) : [],
      sources: [...seed.sources, ...(seed.sources.some((s) => !s.playable_in_app) ? [] : [])],
      attribution: 'Availability data by JustWatch',
    })
  }),
  http.get(`${B}/browse/:section/categories`, () => HttpResponse.json([])),
  http.get(`${B}/browse/:section/languages`, () => HttpResponse.json([{ code: 'eng', name: 'English', count: titles.length }])),
  http.get(`${B}/browse/:section`, async ({ params, request }) => {
    const u = new URL(request.url)
    const page = Number(u.searchParams.get('page') ?? 1)
    const source = u.searchParams.get('source_id')
    const sort = u.searchParams.get('sort')
    const query = u.searchParams.get('q')?.toLowerCase()
    const kind = params.section === 'movies' || params.section === 'vod' ? 'movie' : 'show'
    let list = titles.filter((t) => t.kind === kind && (!query || t.title.toLowerCase().includes(query)) && (!source || t.sources.some((s) => s.source_id === source)))
    if (sort === 'title') list = [...list].sort((a, b) => a.title.localeCompare(b.title))
    if (sort === 'year') list = [...list].sort((a, b) => (b.year ?? 0) - (a.year ?? 0))
    if (sort === 'rating') list = [...list].sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
    const size = 60
    await delay(80)
    return HttpResponse.json({ tiles: list.slice((page - 1) * size, page * size), page, page_size: size, has_more: page * size < list.length, total: list.length })
  }),

  http.get(`${B}/channels`, async ({ request }) => {
    const u = new URL(request.url)
    const group = u.searchParams.get('group')
    const country = u.searchParams.get('country')
    const lang = u.searchParams.get('language')
    const favs = u.searchParams.get('favorites') === 'true'
    const q = u.searchParams.get('q')?.toLowerCase()
    const pid = currentProfileId(request)
    const langs = lang === 'all' ? [] : lang ? lang.split(',') : profiles.find((p) => p.id === pid)?.languages ?? []
    const groupNames = [...new Set(channels.map((c) => c.group))]
    const selectedGroup = group?.startsWith('g') && /^g\d+$/.test(group) ? groupNames[Number(group.slice(1))] : group
    const list = channels
      .filter((c) => countryMatches(c.country, country) && !hiddenGroups.has(c.group) && (!selectedGroup || c.group === selectedGroup) && (!langs.length || langs.includes(c.language)) && (!favs || fav.has(c.id)) && (!q || c.name.toLowerCase().includes(q) || String(c.number) === q))

    const offset = Number(u.searchParams.get('offset') ?? 0), limit = Number(u.searchParams.get('limit') ?? list.length)
    return HttpResponse.json({ list_id: `list:${group ?? 'all'}:${lang ?? 'p'}:${favs}`, channels: list.slice(offset, offset + limit).map(withNowNext), total: list.length, limit, offset })
  }),
  http.get(`${B}/channels/groups`, ({ request }) => {
    const u = new URL(request.url), lang = u.searchParams.get('language'), country = u.searchParams.get('country')
    const langs = lang === 'all' ? [] : lang ? lang.split(',') : profiles.find((p) => p.id === currentProfileId(request))?.languages ?? []
    const q = u.searchParams.get('q')?.toLowerCase()
    const all = [...new Set(channels.map((c) => c.group))]
    return HttpResponse.json(all.map((g, i) => ({ id: `g${i}`, source_id: 's_xt', name: g,
      channel_count: channels.filter((c) => countryMatches(c.country, country) && c.group === g && (!langs.length || langs.includes(c.language)) && (!q || c.name.toLowerCase().includes(q) || String(c.number) === q)).length,
      hidden: hiddenGroups.has(g), language_override: null })).filter((g) => g.channel_count > 0))
  }),
  http.patch(`${B}/channels/groups/:id`, async ({ params, request }) => {
    const b = (await request.json()) as { hidden?: boolean }
    const name = [...new Set(channels.map((c) => c.group))][Number(String(params.id).slice(1))]
    if (name && b.hidden !== undefined) { if (b.hidden) hiddenGroups.add(name); else hiddenGroups.delete(name) }
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
    return HttpResponse.json(Object.fromEntries(ids.map((id) => [id, programmesFor(channels.find((c) => c.id === id) ?? channels[0]!, from, to)])))
  }),

  http.get(`${B}/playback/generation`, () => HttpResponse.json({ generation: 'fixture-generation' })),
  http.post(`${B}/playback/resolve`, async ({ request }) => {
    const b = (await request.json()) as { id: string; source_id?: string | null }
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
    const d: PlaybackDescriptor = {
      ...base, mode: 'direct', url: '/mock/sample.webm', mime: 'video/webm', live: chan,
      resume_position_ms: progress[b.id]?.pos ? Math.min(progress[b.id]!.pos, 20_000) : 0, duration_ms: chan ? null : 30_000,
      channel_context: chan ? { list_id: 'mock', index: channels.findIndex((c) => c.id === b.id), count: channels.length } : null,
    }
    return HttpResponse.json(d)
  }),
  http.post(`${B}/playback/progress`, () => new HttpResponse(null, { status: 204 })),

  http.get(`${B}/favorites`, () => HttpResponse.json([
    ...channels.filter((c) => fav.has(c.id)).map(withNowNext).map(channelTile),
    ...titles.filter((t) => fav.has(t.id)).map((t) => ({ ...t, is_favorite: true })),
  ])),
  http.put(`${B}/favorites`, async ({ request }) => { fav.add(((await request.json()) as { id: string }).id); return new HttpResponse(null, { status: 204 }) }),
  http.delete(`${B}/favorites`, ({ request }) => { fav.delete(new URL(request.url).searchParams.get('id') ?? ''); return new HttpResponse(null, { status: 204 }) }),
  http.get(`${B}/watchlist`, () => HttpResponse.json(titles.filter((t) => watch.has(t.id)))),
  http.put(`${B}/watchlist`, async ({ request }) => { watch.add(((await request.json()) as { id: string }).id); return new HttpResponse(null, { status: 204 }) }),
  http.delete(`${B}/watchlist`, ({ request }) => { watch.delete(new URL(request.url).searchParams.get('id') ?? ''); return new HttpResponse(null, { status: 204 }) }),
  http.get(`${B}/history`, () => HttpResponse.json([])),
  http.get(`${B}/themes`, () => HttpResponse.json([])),
  http.get(`${B}/settings`, () => HttpResponse.json(settings)),
  http.patch(`${B}/settings`, async ({ request }) => { settings = { ...settings, ...((await request.json()) as object) }; return HttpResponse.json(settings) }),
  http.put(`${B}/settings/tmdb_key`, () => { settings.tmdb_api_key_set = true; return new HttpResponse(null, { status: 204 }) }),
]
