import type { Channel, Programme, Source, SourceBadge, Tile, Profile, Row } from '../api/types'
import { backdrop, logo, poster } from './art'

export const DISCLAIMER = {
  version: '1.0',
  text: `**StreamHub is a media player and organizer.** It does not provide, host, sell, promote or distribute any channels, playlists, subscriptions, EPG data or streams.

You may only add sources you are **legally authorized** to access. You are **solely responsible** for the sources you configure and for complying with the laws of your jurisdiction and the rights of content owners. The authors and contributors of StreamHub accept no liability for how you use this software.

Streaming-service names and logos are trademarks of their respective owners. StreamHub is not affiliated with, endorsed by or certified by any of them, including Plex, Jellyfin, Netflix, Hulu, Disney, Amazon, Warner Bros. Discovery, Apple, NBCUniversal or Paramount.

This product uses the TMDB API but is not endorsed or certified by TMDB. Availability ("where to watch") data is provided by JustWatch.`,
}

export const profiles: Profile[] = [
  { id: 'p1', name: 'Ryan', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: null },
  { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng', 'spa'], has_pin: false, kids: false, disclaimer_accepted_version: null },
  { id: 'p3', name: 'Kids', avatar: null, is_admin: false, theme_id: 'oled', languages: ['eng'], has_pin: true, kids: true, disclaimer_accepted_version: null },
]

export const sources: Source[] = [
  { id: 's_plex', type: 'plex', name: 'Den Plex', enabled: true, priority: 1, region: 'US', status: 'ok', last_sync: new Date().toISOString(), counts: { movies: 12, shows: 6 }, service_key: null, config_summary: { server: 'den.local' } },
  { id: 's_xt', type: 'xtream', name: 'IPTV Provider', enabled: true, priority: 2, region: 'US', status: 'ok', last_sync: new Date().toISOString(), counts: { channels: 40, vod: 8 }, service_key: null, config_summary: { server_url: 'http://provider.example:8080', username: 'demo' } },
  { id: 's_nf', type: 'streaming_service', name: 'Netflix', enabled: true, priority: 3, region: 'US', status: 'ok', last_sync: null, counts: {}, service_key: 'netflix', config_summary: {} },
]

const plex = (q = '1080p'): SourceBadge => ({ source_id: 's_plex', source_type: 'plex', name: 'Plex', playable_in_app: true, kind: 'library', quality: q })
const xt: SourceBadge = { source_id: 's_xt', source_type: 'xtream', name: 'IPTV', playable_in_app: true, kind: 'vod', quality: 'HD' }
const nf: SourceBadge = { source_id: 's_nf', source_type: 'streaming_service', name: 'Netflix', playable_in_app: false, kind: 'flatrate' }
const dp: SourceBadge = { source_id: 's_dp', source_type: 'streaming_service', name: 'Disney+', playable_in_app: false, kind: 'flatrate' }

interface Seed { n: number; title: string; year: number; kind: 'movie' | 'show'; overview: string; genres: string[]; runtime: number; sources: SourceBadge[]; rating: number }
const seeds: Seed[] = [
  { n: 101, title: 'Harbor Lights', year: 2024, kind: 'movie', overview: 'A lighthouse keeper on the last manned coast discovers that every ship she guides home is carrying a secret.', genres: ['Drama', 'Mystery'], runtime: 118, sources: [plex('4K')], rating: 7.8 },
  { n: 102, title: 'The Quiet Meridian', year: 2023, kind: 'movie', overview: 'Two cartographers race across a frozen continent to redraw a border before the thaw.', genres: ['Adventure'], runtime: 131, sources: [plex(), nf], rating: 7.2 },
  { n: 103, title: 'Neon Cartographers', year: 2025, kind: 'movie', overview: 'In a city that rewrites its own streets nightly, a courier learns the map is alive.', genres: ['Sci-Fi', 'Thriller'], runtime: 109, sources: [plex(), xt], rating: 8.1 },
  { n: 104, title: 'Salt & Static', year: 2022, kind: 'movie', overview: 'A radio host on a drifting oil platform picks up a broadcast from tomorrow.', genres: ['Sci-Fi'], runtime: 97, sources: [plex()], rating: 6.9 },
  { n: 105, title: 'Field Notes from Mars', year: 2021, kind: 'movie', overview: 'The first botanist on Mars keeps a diary that slowly becomes a love letter to Earth.', genres: ['Drama', 'Sci-Fi'], runtime: 124, sources: [nf], rating: 7.5 },
  { n: 106, title: 'Paper Kingdoms', year: 2020, kind: 'movie', overview: 'An origami artist inherits a palace made entirely of folded maps.', genres: ['Fantasy'], runtime: 102, sources: [plex(), dp], rating: 7.0 },
  { n: 107, title: 'Second Wind', year: 2024, kind: 'movie', overview: 'A retired sprinter coaches the slowest team in the county to a national final.', genres: ['Comedy', 'Sport'], runtime: 106, sources: [plex()], rating: 7.4 },
  { n: 108, title: 'The Understudy', year: 2023, kind: 'movie', overview: 'On opening night the lead vanishes, and the understudy discovers why.', genres: ['Thriller'], runtime: 112, sources: [xt], rating: 6.6 },
  { n: 109, title: 'Gilded Hour', year: 2019, kind: 'movie', overview: 'A clockmaker\'s final commission stops time in a single Venetian afternoon.', genres: ['Romance'], runtime: 98, sources: [plex(), nf], rating: 7.9 },
  { n: 110, title: 'Ember Road', year: 2025, kind: 'movie', overview: 'Six strangers share one ride across a burning state.', genres: ['Action'], runtime: 121, sources: [plex('4K')], rating: 7.1 },
  { n: 111, title: 'Coldframe', year: 2022, kind: 'movie', overview: 'An archivist restores a roll of film that predicts the next day.', genres: ['Mystery'], runtime: 104, sources: [plex()], rating: 7.3 },
  { n: 112, title: 'Tidal Hymn', year: 2024, kind: 'movie', overview: 'A choir on a flooding island sings through the last service.', genres: ['Drama'], runtime: 115, sources: [dp], rating: 8.0 },
  { n: 201, title: 'Low Tide', year: 2024, kind: 'show', overview: 'Every episode, one more thing washes ashore in a small harbor town, and someone local recognizes it.', genres: ['Drama', 'Mystery'], runtime: 48, sources: [plex(), nf], rating: 8.4 },
  { n: 202, title: 'Orbit Street', year: 2023, kind: 'show', overview: 'Neighbors on a street that floats in low orbit run the world\'s strangest block association.', genres: ['Comedy', 'Sci-Fi'], runtime: 28, sources: [plex()], rating: 8.0 },
  { n: 203, title: 'The Understudies', year: 2025, kind: 'show', overview: 'A touring company of backups, each convinced they are the real star.', genres: ['Comedy'], runtime: 32, sources: [plex(), xt], rating: 7.7 },
  { n: 204, title: 'Signal Fire', year: 2022, kind: 'show', overview: 'Wildfire lookouts form a secret network that tracks more than smoke.', genres: ['Thriller'], runtime: 52, sources: [nf], rating: 7.9 },
  { n: 205, title: 'Quarry', year: 2021, kind: 'show', overview: 'A rural detective reopens a cold case with the help of the quarry\'s oldest blaster.', genres: ['Crime'], runtime: 50, sources: [plex()], rating: 8.2 },
  { n: 206, title: 'Moss & Marrow', year: 2024, kind: 'show', overview: 'A forest ranger and a chef trade recipes for survival.', genres: ['Documentary'], runtime: 40, sources: [plex(), dp], rating: 7.6 },
]

export const progress: Record<string, { pos: number; dur: number }> = {
  'tmdb:movie:101': { pos: 48 * 60_000, dur: 118 * 60_000 },
  'tmdb:movie:103': { pos: 20 * 60_000, dur: 109 * 60_000 },
  'tmdb:episode:201:1:2': { pos: 15 * 60_000, dur: 48 * 60_000 },
}

export const fav = new Set<string>(['tmdb:movie:101', 'ch:s_xt:1002', 'ch:s_xt:1005'])
export const watch = new Set<string>(['tmdb:movie:105', 'tmdb:tv:204', 'tmdb:movie:112'])

const idOf = (s: Seed) => (s.kind === 'movie' ? `tmdb:movie:${s.n}` : `tmdb:tv:${s.n}`)

export function tileOf(s: Seed): Tile {
  const id = idOf(s)
  const pr = progress[id]
  return {
    id, kind: s.kind, title: s.title, subtitle: s.genres[0] ?? null, year: s.year, poster: poster(s.title, s.year), backdrop: backdrop(s.title),
    logo: null, rating: s.rating, progress_pct: pr ? (pr.pos / pr.dur) * 100 : null, is_favorite: fav.has(id), sources: s.sources,
  }
}

export const titles = seeds.map(tileOf)
export const seedFor = (id: string) => seeds.find((s) => idOf(s) === id)

export function episodesOf(show: number, season: number): Tile[] {
  const s = seeds.find((x) => x.n === show)
  if (!s) return []
  return Array.from({ length: 6 }, (_, i) => {
    const id = `tmdb:episode:${show}:${season}:${i + 1}`
    const pr = progress[id]
    return {
      id, kind: 'episode' as const, title: `${i + 1}. ${['Landfall', 'Slack Water', 'What the Net Held', 'Glass Floats', 'The Long Ebb', 'Spring Tide'][i]}`,
      subtitle: `S${season} · E${i + 1}`, year: s.year, poster: null, backdrop: backdrop(`${s.title}${season}${i}`), progress_pct: pr ? (pr.pos / pr.dur) * 100 : null,
      is_favorite: false, sources: s.sources,
    }
  })
}
export const seasonsOf = (show: number): Tile[] =>
  [1, 2].map((n) => ({ id: `tmdb:season:${show}:${n}`, kind: 'season' as const, title: `Season ${n}`, poster: null, backdrop: null, sources: [], is_favorite: false }))

/* ---------- live ---------- */
const groups = ['News', 'Sports', 'Movies', 'Kids', 'Music', 'Local']
const langs = ['eng', 'eng', 'eng', 'spa', 'fra', 'deu', 'hin']
const chanNames = [
  'Harbor News 24', 'Metro Sports 1', 'Cinema Classics', 'Little Orbit', 'Static Radio TV', 'Channel 9 Local', 'World Report',
  'Court Vision', 'Midnight Movies', 'Cartoon Pier', 'Indie Beats', 'County Weather', 'Business Today', 'Sunday League',
  'Retro Reels', 'Tiny Explorers', 'Jazz Lounge', 'City Council TV', 'Noticias Hoy', 'Deporte Total', 'Cine Latino', 'Pequeños',
  'Info France', 'Le Foot', 'Ciné Paris', 'Tagesblick', 'Bundesliga Live', 'Kino Nacht', 'Desi Beat', 'Bollywood Now',
  'Science Hour', 'Cooking Daily', 'Travel Atlas', 'Nature Loop', 'Docu One', 'Comedy Corner', 'Game Night', 'Old Time TV', 'Arts Hall', 'Late Show Live',
]
export const channels: Channel[] = chanNames.map((name, i) => {
  const lang = i < 18 ? 'eng' : (['spa', 'spa', 'spa', 'spa', 'fra', 'fra', 'fra', 'deu', 'deu', 'deu', 'hin', 'hin'][i - 18] ?? langs[i % langs.length]!)
  const num = 1000 + i + 1
  return {
    id: `ch:s_xt:${num}`, source_id: 's_xt', ext_id: String(num), number: i + 1, name, logo: logo(name), group: groups[i % groups.length]!,
    language: lang, language_source: 'tag', country: lang === 'eng' ? 'US' : null, epg_id: `epg${num}`, catchup_days: i % 3 === 0 ? 2 : 0,
    is_favorite: fav.has(`ch:s_xt:${num}`),
  }
})

const showNames = ['Morning Brief', 'The Daily Cut', 'Match Day', 'Classic Feature', 'Evening Edition', 'Studio Session', 'Late Feature', 'Kids Hour', 'The Long Take', 'Night Owl']
export function programmesFor(ch: Channel, fromMs: number, toMs: number): Programme[] {
  const out: Programme[] = []
  const slot = 30 * 60_000
  const seed = ch.number * 7
  let t = Math.floor(fromMs / slot) * slot - ((seed % 3) * slot)
  let i = 0
  while (t < toMs) {
    const len = (1 + ((seed + i) % 3)) * slot
    const start = Math.max(t, t)
    out.push({
      id: `${ch.id}@${start}`, channel_id: ch.id, start: new Date(start).toISOString(), stop: new Date(start + len).toISOString(),
      title: `${showNames[(seed + i) % showNames.length]}${i % 4 === 0 ? ' (Live)' : ''}`, description: 'Fixture programme. In mock mode every channel plays the same test pattern.',
      category: ch.group, icon: null, catchup_available: ch.catchup_days > 0 && start + len < Date.now(),
    })
    t += len
    i++
  }
  return out
}
export function withNowNext(ch: Channel): Channel {
  const now = Date.now()
  const list = programmesFor(ch, now - 3 * 3600_000, now + 3 * 3600_000)
  const cur = list.findIndex((p) => Date.parse(p.start) <= now && Date.parse(p.stop) > now)
  return { ...ch, is_favorite: fav.has(ch.id), now: list[cur], next: list[cur + 1] }
}
export const channelTile = (c: Channel): Tile => ({
  id: c.id, kind: 'channel', title: c.name, subtitle: String(c.number), logo: c.logo, is_favorite: fav.has(c.id),
  sources: [{ source_id: c.source_id, source_type: 'xtream', name: 'IPTV', playable_in_app: true, kind: 'live' }], now_playing: c.now,
})

export function homeRows(): { hero: Tile[]; rows: Row[] } {
  const all: Tile[] = titles.map((t) => ({ ...t, is_favorite: fav.has(t.id), progress_pct: progress[t.id] ? (progress[t.id]!.pos / progress[t.id]!.dur) * 100 : null }))
  const by = (ids: string[]) => ids.map((id) => all.find((t) => t.id === id)).filter((t): t is Tile => !!t)
  const favChannels = channels.filter((c) => fav.has(c.id)).map(withNowNext).map(channelTile)
  const moreFavs = channels.slice(0, 6).map(withNowNext).map(channelTile)
  return {
    hero: by(['tmdb:movie:103', 'tmdb:tv:201', 'tmdb:movie:101', 'tmdb:tv:205']),
    rows: [
      { id: 'continue', title: 'Continue Watching', layout: 'landscape', tiles: by(['tmdb:movie:101', 'tmdb:movie:103']) },
      { id: 'onnow', title: 'On Now', layout: 'channel', tiles: [...favChannels, ...moreFavs.filter((c) => !favChannels.some((f) => f.id === c.id))].slice(0, 8) },
      { id: 'recent', title: 'Recently Added', layout: 'poster', tiles: all.slice(0, 12) },
      { id: 'watchlist', title: 'Watchlist', layout: 'poster', tiles: by([...watch]) },
      { id: 'favorites', title: 'Favorites', layout: 'poster', tiles: all.filter((t) => fav.has(t.id)) },
      { id: 'src:s_plex', title: 'From Den Plex', layout: 'poster', tiles: all.filter((t) => t.sources.some((s) => s.source_id === 's_plex')).slice(0, 14) },
      { id: 'src:s_nf', title: 'On Netflix', layout: 'poster', tiles: all.filter((t) => t.sources.some((s) => s.source_id === 's_nf')) },
    ],
  }
}
