import type { Channel, Programme, SearchProgramme, Shelf, Source, SourceBadge, Tile, Profile, Row } from '../api/types'
import { backdrop, logo, poster } from './art'

export const DISCLAIMER = {
  version: '1.0',
  text: `**StreamHub is a media player and organizer.** It does not provide, host, sell, promote or distribute any channels, playlists, subscriptions, EPG data or streams.

You may only add sources you are **legally authorized** to access. You are **solely responsible** for the sources you configure and for complying with the laws of your jurisdiction and the rights of content owners. The authors and contributors of StreamHub accept no liability for how you use this software.

Streaming-service names and logos are trademarks of their respective owners. StreamHub is not affiliated with, endorsed by or certified by any of them, including Plex, Jellyfin, Netflix, Hulu, Disney, Amazon, Warner Bros. Discovery, Apple, NBCUniversal or Paramount.

This product uses the TMDB API but is not endorsed or certified by TMDB. Availability ("where to watch") data is provided by JustWatch.`,
}

export const profiles: Profile[] = [
  { id: 'p1', name: 'Ryan', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, has_password: true, kids: false, disclaimer_accepted_version: null },
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

interface Seed { n: number; title: string; year: number; kind: 'movie' | 'show'; overview: string; genres: string[]; runtime: number; sources: SourceBadge[]; rating: number; shelf?: string }
const seeds: Seed[] = [
  { n: 101, title: 'Harbor Lights', year: 2024, kind: 'movie', overview: 'A lighthouse keeper on the last manned coast discovers that every ship she guides home is carrying a secret.', genres: ['Drama', 'Mystery'], runtime: 118, sources: [plex('4K')], rating: 7.8, shelf: 'Drama' },
  { n: 102, title: 'The Quiet Meridian', year: 2023, kind: 'movie', overview: 'Two cartographers race across a frozen continent to redraw a border before the thaw.', genres: ['Adventure'], runtime: 131, sources: [plex(), nf], rating: 7.2, shelf: 'Action' },
  { n: 103, title: 'Neon Cartographers', year: 2025, kind: 'movie', overview: 'In a city that rewrites its own streets nightly, a courier learns the map is alive.', genres: ['Sci-Fi', 'Thriller'], runtime: 109, sources: [plex(), xt], rating: 8.1, shelf: 'New Releases' },
  { n: 104, title: 'Salt & Static', year: 2022, kind: 'movie', overview: 'A radio host on a drifting oil platform picks up a broadcast from tomorrow.', genres: ['Sci-Fi'], runtime: 97, sources: [plex()], rating: 6.9, shelf: 'Thriller' },
  { n: 105, title: 'Field Notes from Mars', year: 2021, kind: 'movie', overview: 'The first botanist on Mars keeps a diary that slowly becomes a love letter to Earth.', genres: ['Drama', 'Sci-Fi'], runtime: 124, sources: [nf], rating: 7.5, shelf: 'Drama' },
  { n: 106, title: 'Paper Kingdoms', year: 2020, kind: 'movie', overview: 'An origami artist inherits a palace made entirely of folded maps.', genres: ['Fantasy'], runtime: 102, sources: [plex(), dp], rating: 7.0, shelf: 'Kids' },
  { n: 107, title: 'Second Wind', year: 2024, kind: 'movie', overview: 'A retired sprinter coaches the slowest team in the county to a national final.', genres: ['Comedy', 'Sport'], runtime: 106, sources: [plex()], rating: 7.4, shelf: 'Comedy' },
  { n: 108, title: 'The Understudy', year: 2023, kind: 'movie', overview: 'On opening night the lead vanishes, and the understudy discovers why.', genres: ['Thriller'], runtime: 112, sources: [xt], rating: 6.6, shelf: 'Thriller' },
  { n: 109, title: 'Gilded Hour', year: 2019, kind: 'movie', overview: 'A clockmaker\'s final commission stops time in a single Venetian afternoon.', genres: ['Romance'], runtime: 98, sources: [plex(), nf], rating: 7.9, shelf: 'Drama' },
  { n: 110, title: 'Ember Road', year: 2025, kind: 'movie', overview: 'Six strangers share one ride across a burning state.', genres: ['Action'], runtime: 121, sources: [plex('4K')], rating: 7.1, shelf: 'New Releases' },
  { n: 111, title: 'Coldframe', year: 2022, kind: 'movie', overview: 'An archivist restores a roll of film that predicts the next day.', genres: ['Mystery'], runtime: 104, sources: [plex()], rating: 7.3, shelf: 'Thriller' },
  { n: 112, title: 'Tidal Hymn', year: 2024, kind: 'movie', overview: 'A choir on a flooding island sings through the last service.', genres: ['Drama'], runtime: 115, sources: [dp], rating: 8.0, shelf: 'Drama' },
  { n: 201, title: 'Low Tide', year: 2024, kind: 'show', overview: 'Every episode, one more thing washes ashore in a small harbor town, and someone local recognizes it.', genres: ['Drama', 'Mystery'], runtime: 48, sources: [plex(), nf], rating: 8.4, shelf: 'TV Shows' },
  { n: 202, title: 'Orbit Street', year: 2023, kind: 'show', overview: 'Neighbors on a street that floats in low orbit run the world\'s strangest block association.', genres: ['Comedy', 'Sci-Fi'], runtime: 28, sources: [plex()], rating: 8.0, shelf: 'TV Shows' },
  { n: 203, title: 'The Understudies', year: 2025, kind: 'show', overview: 'A touring company of backups, each convinced they are the real star.', genres: ['Comedy'], runtime: 32, sources: [plex(), xt], rating: 7.7, shelf: 'Comedy' },
  { n: 204, title: 'Signal Fire', year: 2022, kind: 'show', overview: 'Wildfire lookouts form a secret network that tracks more than smoke.', genres: ['Thriller'], runtime: 52, sources: [nf], rating: 7.9, shelf: 'Netflix' },
  { n: 205, title: 'Quarry', year: 2021, kind: 'show', overview: 'A rural detective reopens a cold case with the help of the quarry\'s oldest blaster.', genres: ['Crime'], runtime: 50, sources: [plex()], rating: 8.2, shelf: 'TV Shows' },
  { n: 206, title: 'Moss & Marrow', year: 2024, kind: 'show', overview: 'A forest ranger and a chef trade recipes for survival.', genres: ['Documentary'], runtime: 40, sources: [plex(), dp], rating: 7.6, shelf: 'Documentaries' },
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

// Known to the metadata provider, owned by no source: what a viewer can request.
// Kept out of `titles` so library, browse and home fixtures stay what they were.
const unavailableSeeds: Seed[] = [
  { n: 301, title: 'Velvet Antarctica', year: 2025, kind: 'movie', overview: 'A sound recordist winters alone at a research station and starts to hear the ice answer back.', genres: ['Adventure', 'Mystery'], runtime: 114, sources: [], rating: 7.6 },
  { n: 302, title: 'The Tollgate Tapes', year: 2024, kind: 'show', overview: 'A toll collector keeps every dashcam tape handed in at her booth. One of them is evidence.', genres: ['Crime'], runtime: 45, sources: [], rating: 8.1 },
  { n: 303, title: 'Sunday League Legends', year: 2023, kind: 'show', overview: 'Five amateur sides, one muddy pitch, and the season none of them will stop talking about.', genres: ['Documentary'], runtime: 30, sources: [], rating: 7.3 },
]
export const unavailable = unavailableSeeds.map(tileOf)

// Enough further titles to fill shelves the way a provider catalog does. Like the titles above they are
// invented; they stay out of `titles` so the grid, search and discovery fixtures keep their small, known set.
const shelved = (kind: Seed['kind'], first: number, shelf: string, year: number, names: string[]): Seed[] =>
  names.map((title, i) => ({
    n: first + i, title, year, kind, overview: `${title}: a fixture title on the ${shelf} shelf.`, genres: [shelf], runtime: kind === 'movie' ? 96 + i * 7 : 42,
    sources: [i % 3 === 2 ? xt : plex()], rating: 6.4 + ((first + i) % 9) / 5, shelf,
  }))
const shelfSeeds: Seed[] = [
  ...shelved('movie', 401, 'New Releases', 2025, ['Glass Estuary', 'The Last Semaphore', 'Winter Almanac', 'Copper Sky', 'Lantern District', 'A Field of Radios']),
  ...shelved('movie', 411, 'Netflix', 2024, ['Midnight Ledger', 'The Paper Astronaut', 'Slow Thunder', 'Borrowed Summer', 'Nine Rooms North']),
  ...shelved('movie', 421, 'Drama', 2022, ['Ashgrove', 'The Weight of Rain', 'Small Hours Diner']),
  ...shelved('movie', 431, 'Action', 2023, ['Redline Ferry', 'Iron Monsoon', 'Vector Nine']),
  ...shelved('movie', 441, 'Comedy', 2021, ['Two Left Shoes', 'The Wrong Wedding Band', 'Mostly Capable Dave']),
  ...shelved('movie', 451, 'Kids', 2020, ['Captain Biscuit', 'The Puddle Pirates']),
  ...shelved('movie', 461, 'Documentaries', 2023, ['Rivers Without Maps', 'The Honey Line', 'Ninety Days of Ice']),
  ...shelved('show', 501, 'TV Shows', 2024, ['Pier Watch', 'Fifth Floor Walkup', 'The Long Table', 'Night Shift Radio', 'Gravel Roads']),
  ...shelved('show', 511, 'Netflix', 2023, ['Paper Trail Club', 'Static Bloom', 'The Lamplighters Guild']),
  ...shelved('show', 521, 'Kids', 2022, ['Tiny Tugboats', 'Professor Pickle']),
]
const shelfCatalog = [...seeds, ...shelfSeeds]
export const seedFor = (id: string) => [...shelfCatalog, ...unavailableSeeds].find((s) => idOf(s) === id)

/** Every title of one shelf, newest first: what "See all" lists. */
export const shelfTiles = (kind: Seed['kind'], shelf: string): Tile[] =>
  shelfCatalog.filter((s) => s.kind === kind && s.shelf === shelf).sort((a, b) => b.year - a.year || a.n - b.n).map(tileOf)

/** Shelves of a section as the server ranks them: New Releases, then the fullest first. */
export function shelvesOf(section: 'movies' | 'shows'): Shelf[] {
  const kind = section === 'movies' ? 'movie' : 'show'
  const names = [...new Set(shelfCatalog.filter((s) => s.kind === kind && s.shelf).map((s) => s.shelf!))]
  return names
    .map((name) => ({ name, items: shelfTiles(kind, name) }))
    .sort((a, b) => Number(b.name === 'New Releases') - Number(a.name === 'New Releases') || b.items.length - a.items.length || a.name.localeCompare(b.name))
    .map(({ name, items }) => ({ name, count: items.length, items: items.slice(0, 20), browse_path: `/browse/${section}?category=${encodeURIComponent(name)}` }))
}

// Marked adult by the provider: never in a listing unless include_adult is honoured.
const adultSeeds = shelved('movie', 471, 'After Dark', 2024, ['Velvet Hours', 'The Night Porter Returns', 'Room 9'])
export const adultShelf = (section: 'movies' | 'shows'): Shelf[] =>
  section === 'movies' ? [{ name: 'After Dark', count: adultSeeds.length, items: adultSeeds.map(tileOf), browse_path: '/browse/movies?category=After%20Dark' }] : []

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
// Sports is a parent: some of its channels are its own, the rest sit in three children (`Parent / Child`).
const nested: Record<number, string> = { 7: 'Sports / Basketball', 13: 'Sports / Football', 19: 'Sports / Football', 37: 'Sports / Hockey' }
/** The server's reading of a group name: only the first ` / ` separates the two levels. */
export function groupLevels(path: string): { parent: string | null; name: string } {
  const at = path.indexOf(' / ')
  return at > 0 && at + 3 < path.length ? { parent: path.slice(0, at), name: path.slice(at + 3) } : { parent: null, name: path }
}
/** The top-level group a channel is in, whichever level it is stored under. */
export const topGroup = (path: string): string => groupLevels(path).parent ?? path
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
    id: `ch:s_xt:${num}`, source_id: 's_xt', ext_id: String(num), number: i + 1, name, logo: logo(name), group: nested[i] ?? groups[i % groups.length]!,
    language: lang, language_source: 'tag', country: lang === 'eng' ? 'US' : null, epg_id: `epg${num}`, catchup_days: i % 3 === 0 ? 2 : 0,
    is_favorite: fav.has(`ch:s_xt:${num}`),
  }
})

const showNames = ['Morning Brief', 'The Daily Cut', 'Match Day', 'Classic Feature', 'Evening Edition', 'Studio Session', 'Late Feature', 'Kids Hour', 'The Long Take', 'Night Owl']
// A sports channel alternates a game that names its two sides with a listing that names none.
const games = ['Riverton Rovers vs Harbor City', 'Sports Tonight', 'Premier Cup: Eastgate Town v Westbrook', 'Match Day', 'Northfield United at Lakeside Athletic', 'The Final Whistle']
/** The server's reading of a sports title: the two sides of a spelled-out matchup, league prefix and trailing notes dropped; else none. */
export function teamsOf(title: string): string[] {
  const plain = title.replace(/\s*\([^)]*\)\s*$/, '').replace(/^[^:]*:\s*/, '')
  const sides = /^(.+?)\s+(?:vs\.?|v|versus|at|@)\s+(.+)$/i.exec(plain)
  return sides ? [sides[1]!.trim(), sides[2]!.trim()] : []
}
/** A channel's schedule is the same whatever span is asked for, as a server's is: the guide asks for a few hours at a time. */
export function programmesFor(ch: Channel, fromMs: number, toMs: number): Programme[] {
  const out: Programme[] = []
  const slot = 30 * 60_000
  const seed = ch.number * 7
  const sport = topGroup(ch.group) === 'Sports'
  const names = sport ? games : showNames
  // Programmes of one, two and three slots, in an order the channel sets, repeat every three hours from a fixed origin.
  const cycle = 6 * slot, shift = (seed % 3) * slot
  for (let k = Math.floor((fromMs + shift) / cycle); k * cycle - shift < toMs; k++) {
    let start = k * cycle - shift
    for (let j = 0; j < 3; j++) {
      const i = k * 3 + j
      const len = (1 + ((seed + i) % 3)) * slot
      if (start + len > fromMs && start < toMs) {
        const title = `${names[(seed + i) % names.length]}${i % 4 === 0 ? ' (Live)' : ''}`
        out.push({
          id: `${ch.id}@${start}`, channel_id: ch.id, start: new Date(start).toISOString(), stop: new Date(start + len).toISOString(),
          title, description: 'Fixture programme. In mock mode every channel plays the same test pattern.',
          category: topGroup(ch.group), icon: null, catchup_available: ch.catchup_days > 0 && start + len < Date.now(), is_sport: sport, teams: sport ? teamsOf(title) : [],
        })
      }
      start += len
    }
  }
  return out
}
export function withNowNext(ch: Channel): Channel {
  const now = Date.now()
  const list = programmesFor(ch, now - 3 * 3600_000, now + 3 * 3600_000)
  const cur = list.findIndex((p) => Date.parse(p.start) <= now && Date.parse(p.stop) > now)
  return { ...ch, is_favorite: fav.has(ch.id), now: list[cur], next: list[cur + 1] }
}
// One listing the repeating schedule does not have: a broadcast a day ahead, on a channel other than the one it names.
const AHEAD = { title: 'Sunday League Live', channel: 1, inHours: 26, hours: 2 }
/** What a search finds in the guide: titles containing the query, on now or within two days, on now first.
 *  One entry per title and start; the channels carrying it are its feeds, lowest number first. */
export function searchBroadcasts(q: string): SearchProgramme[] {
  const now = Date.now()
  const found: Array<{ p: Programme; ch: Channel }> = []
  for (const ch of channels) for (const p of programmesFor(ch, now, now + 48 * 3600_000)) if (Date.parse(p.stop) > now && p.title.toLowerCase().includes(q)) found.push({ p, ch })
  const on = channels[AHEAD.channel]!
  const start = Math.ceil(now / 3600_000) * 3600_000 + AHEAD.inHours * 3600_000
  if (AHEAD.title.toLowerCase().includes(q)) found.push({ ch: on, p: {
    id: `${on.id}@${start}`, channel_id: on.id, start: new Date(start).toISOString(), stop: new Date(start + AHEAD.hours * 3600_000).toISOString(),
    title: AHEAD.title, description: null, category: 'Sports', icon: null, catchup_available: false, is_sport: true, teams: [],
  } })
  const groups = new Map<string, Array<{ p: Programme; ch: Channel }>>()
  for (const hit of found) groups.set(`${hit.p.title.toLowerCase()}|${hit.p.start}`, [...(groups.get(`${hit.p.title.toLowerCase()}|${hit.p.start}`) ?? []), hit])
  return [...groups.values()].map((hits) => {
    const feeds = [...hits].sort((a, b) => a.ch.number - b.ch.number)
    const { p, ch } = feeds[0]!
    return {
      ...p, on_now: Date.parse(p.start) <= now, channel_number: ch.number, channel_name: ch.name, channel_logo: ch.logo ?? null,
      feeds: feeds.map((f) => ({ programme_id: f.p.id, channel_id: f.ch.id, channel_number: f.ch.number, channel_name: f.ch.name, channel_logo: f.ch.logo ?? null })),
    }
  }).sort((a, b) => Number(b.on_now) - Number(a.on_now) || Date.parse(a.start) - Date.parse(b.start) || a.title.localeCompare(b.title))
}
export const channelTile = (c: Channel): Tile => ({
  id: c.id, kind: 'channel', title: c.name, subtitle: String(c.number), logo: c.logo, is_favorite: fav.has(c.id),
  sources: [{ source_id: c.source_id, source_type: 'xtream', name: 'IPTV', playable_in_app: true, kind: 'live' }], now_playing: c.now,
})

/** `chosen`: the home_shelves setting (Name, movies:Name, shows:Name). Empty: Home chooses, as below. */
export function homeRows(chosen: string[] = []): { hero: Tile[]; rows: Row[] } {
  const all: Tile[] = titles.map((t) => ({ ...t, is_favorite: fav.has(t.id), progress_pct: progress[t.id] ? (progress[t.id]!.pos / progress[t.id]!.dur) * 100 : null }))
  const by = (ids: string[]) => ids.map((id) => all.find((t) => t.id === id)).filter((t): t is Tile => !!t)
  const favChannels = channels.filter((c) => fav.has(c.id)).map(withNowNext).map(channelTile)
  const moreFavs = channels.slice(0, 6).map(withNowNext).map(channelTile)
  // Like the server: New Releases, then up to eight shelves alternating shows and movies.
  const movies = shelvesOf('movies'), shows = shelvesOf('shows')
  const onBoth = new Set(movies.map((s) => s.name).filter((name) => shows.some((s) => s.name === name)))
  const shelfRow = (section: 'movies' | 'shows', shelf: Shelf): Row => ({
    id: `shelf:${section}:${shelf.name}`, title: onBoth.has(shelf.name) ? `${shelf.name} · ${section === 'movies' ? 'Movies' : 'Shows'}` : shelf.name,
    layout: 'poster', tiles: shelf.items, browse_path: shelf.browse_path,
  })
  const [newReleases, ...otherMovies] = movies
  const picked: Row[] = []
  if (chosen.length) {
    // The owner's list, in order, at most twelve; a name no shelf has is skipped.
    for (const entry of chosen) {
      const sided = /^(movies|shows):(.+)$/.exec(entry)
      for (const section of sided ? [sided[1] as 'movies' | 'shows'] : (['movies', 'shows'] as const)) {
        const shelf = (section === 'movies' ? movies : shows).find((s) => s.name === (sided ? sided[2] : entry))
        if (shelf && picked.length < 12 && !(section === 'movies' && shelf === newReleases)) picked.push(shelfRow(section, shelf))
      }
    }
  } else for (let i = 0; picked.length < 8 && (shows[i] || otherMovies[i]); i++) {
    if (shows[i]) picked.push(shelfRow('shows', shows[i]!))
    if (otherMovies[i] && picked.length < 8) picked.push(shelfRow('movies', otherMovies[i]!))
  }
  return {
    hero: newReleases!.items.filter((t) => t.backdrop && (t.rating ?? 0) >= 6).slice(0, 8),
    rows: [
      { id: 'continue', title: 'Continue Watching', layout: 'landscape', tiles: by(['tmdb:movie:101', 'tmdb:movie:103']) },
      { id: 'onnow', title: 'On Now', layout: 'channel', tiles: [...favChannels, ...moreFavs.filter((c) => !favChannels.some((f) => f.id === c.id))].slice(0, 8), browse_path: '/live' },
      shelfRow('movies', newReleases!),
      ...picked,
      { id: 'watchlist', title: 'Watchlist', layout: 'poster', tiles: by([...watch]), browse_path: '/favorites?tab=watchlist' },
      { id: 'favorites', title: 'Favorites', layout: 'poster', tiles: all.filter((t) => fav.has(t.id)), browse_path: '/favorites' },
      { id: 'recent', title: 'Recently Added', layout: 'poster', tiles: all.slice(0, 12) },
    ],
  }
}
