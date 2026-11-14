import type { components } from '../api/schema'
import type { Channel, Programme } from '../api/types'
import { logo, matchupArt } from './art'

type SportsEvent = components['schemas']['SportsEvent']
type SportsFeed = components['schemas']['SportsEventFeed']
type SportsChannel = components['schemas']['SportsChannel']
type Matchup = components['schemas']['SportsMatchup']
type MatchupTeam = components['schemas']['MatchupTeam']

/**
 * A provider's game-day line-up as the hub stores it, and the reading the server makes of it:
 * numbered event slots renamed for each game, one name left over from weeks ago, game channels
 * whose name carries no date, and always-on networks. The guide of every one of them is filler
 * that repeats the channel's name, apart from one real listing. None of that filler, and nothing
 * of the stale name, may reach the Sports screen.
 */
const MIN = 60_000, HOUR = 60 * MIN, DAY = 24 * HOUR
const EVENT_LENGTH = 210 * MIN
const BOOT = Date.now()
const localDay = (at: number) => { const d = new Date(at); d.setHours(0, 0, 0, 0); return d.getTime() }
/** Later today, held inside today so a late-evening session still has a "Today" list. */
const later = (minutes: number) => Math.min(BOOT + minutes * MIN, localDay(BOOT) + DAY - MIN)
const onDay = (days: number, hour: number, minute = 0) => localDay(BOOT) + days * DAY + hour * HOUR + minute * MIN

interface Slot {
  ext: number; number: number; stored: string; shown: string; quality: SportsFeed['quality']; language: string
  league: string; sport: string; title: string; home: string | null; away: string | null; start: number | null
  replay?: boolean; matchup?: Matchup
}
const stamp = (at: number) => new Date(at).toISOString().slice(0, 19).replace('T', ' ')
const slot = (ext: number, network: string, n: string, sport: string, title: string, start: number | null, extra: Partial<Slot> = {}): Slot => {
  const sides = /^(.+?)\s+(at|vs)\s+(.+)$/.exec(title)
  return {
    ext, number: 900 + ext, shown: `${network} ${n}`, quality: null, language: 'eng', league: network, sport, title, start,
    stored: start === null ? `${network} | ${n} - 1pm ${title}` : `(XX) (${network} ${n}) | ${sport}: ${title} (${stamp(start)})`,
    home: sides ? (sides[2] === 'at' ? sides[3]! : sides[1]!) : null, away: sides ? (sides[2] === 'at' ? sides[1]! : sides[3]!) : null,
    ...extra,
  }
}
const slots: Slot[] = [
  slot(1, 'VANTIX+', '001', 'Field Hockey', 'Northfield at Lakeside', BOOT - 40 * MIN),
  slot(15, 'LUMEX TV', '007', 'Field Hockey', 'Northfield at Lakeside', BOOT - 40 * MIN, { quality: 'HD' }),
  slot(4, 'VANTIX+', '004', 'Football', 'Riverton Rovers vs Harbor City', BOOT - 80 * MIN, { quality: 'FHD' }),
  slot(3, 'VANTIX+', '003', "Men's Soccer", 'Oakdale at Port Linden', later(90)),
  slot(2, 'VANTIX+', '002', 'Football', 'Football Weekly', later(180), { quality: 'HD' }),
  slot(5, 'VANTIX+', '005', 'Lacrosse', 'Millbrook at Lakeside', onDay(1, 13)),
  slot(6, 'VANTIX+', '006', 'Rugby', 'Otters vs Herons', onDay(1, 19, 30)),
  slot(16, 'LUMEX TV', '008', 'Rugby', 'Otters vs Herons', onDay(1, 19, 30), { quality: 'HD' }),
  slot(7, 'VANTIX+', '007', 'Basketball', 'Eastgate Town at Westbrook', onDay(2, 18)),
  // Six weeks old: the provider never renamed the slot. Not an event on any day.
  slot(8, 'VANTIX+', '008', "Men's Soccer", 'Stale Town at Old Harbor', BOOT - 42 * DAY),
  slot(21, 'CORVAN LEAGUE', '02', 'Sports', 'Kestrels at Badgers', null, { quality: 'HD' }),
]

/**
 * Football games with team facts, as the server annotates them from the public scoreboard (every team
 * is made up): a ranked side against an unranked one with art and live odds, a game without odds or
 * art yet (the colours stand in), a replay (no clock, no odds), and games still to come. Listed only
 * when sessionStorage['mock.sports'] is 'matchups', so the default line-up stays as it is.
 */
const team = (name: string, short_name: string, color: string, alt_color: string, rank: number | null, record: string, conference_record: string | null): MatchupTeam =>
  ({ name, short_name, color, alt_color, rank, record, conference_record })
const KESTRELS = team('Lakeview Kestrels', 'Lakeview', '#2b4c9a', '#ffffff', null, '6-3', '4-1 Lakes League')
const BADGERS = team('Brookfield Badgers', 'Brookfield', '#7a1f3d', '#f2c14e', 12, '8-1', '5-1 Lakes League')
const HERONS = team('Harbor Herons', 'Harbor', '#e05a1b', '#1b1b1b', 5, '9-0', '6-0')
const OTTERS = team('Riverside Otters', 'Riverside', '#0f6b5c', '#c0c0c0', null, '4-5', '2-3')
const OWLS = team('Northfield Owls', 'Northfield', '#3d2a6b', '#d9d9d9', 18, '7-2', '4-2 Summit Conference')
const FOXES = team('Southgate Foxes', 'Southgate', '#b5651d', '#222222', null, '5-4', '3-3 Summit Conference')
const ELKS = team('Eastgate Elks', 'Eastgate', '#1f5f99', '#f0f0f0', 8, '8-1', '5-0 Summit Conference')
const WOLVES = team('Westbrook Wolves', 'Westbrook', '#5a5a5a', '#c8102e', 21, '7-2', '4-1 Summit Conference')
const matchupSlots: Slot[] = [
  slot(41, 'VANTIX+', '041', 'College Football', 'Lakeview Kestrels at Brookfield Badgers', BOOT - 50 * MIN, { quality: 'HD', matchup: {
    away: KESTRELS, home: BADGERS, status_detail: 'Q2 7:14', win_probability: { away: 14.6, home: 85.4, live: true }, art_url: matchupArt('Lakeview Kestrels at Brookfield Badgers'),
  } }),
  slot(42, 'LUMEX TV', '042', 'College Football', 'Harbor Herons at Riverside Otters', BOOT - 12 * MIN, { matchup: {
    away: HERONS, home: OTTERS, status_detail: 'Q1 3:52', art_url: null,
  } }),
  slot(43, 'VANTIX+', '043', 'College Football', 'Replay: Northfield Owls at Southgate Foxes', BOOT - 30 * MIN, { replay: true, home: 'Southgate Foxes', away: 'Northfield Owls', matchup: {
    away: OWLS, home: FOXES, status_detail: null, art_url: matchupArt('Northfield Owls at Southgate Foxes'),
  } }),
  slot(44, 'VANTIX+', '044', 'College Football', 'Eastgate Elks at Westbrook Wolves', later(150), { matchup: {
    away: ELKS, home: WOLVES, art_url: null,
  } }),
  slot(45, 'LUMEX TV', '045', 'College Football', 'Riverside Otters at Lakeview Kestrels', onDay(1, 15, 30), { matchup: {
    away: OTTERS, home: KESTRELS, art_url: matchupArt('Riverside Otters at Lakeview Kestrels'),
  } }),
]
const withMatchups = () => { try { return sessionStorage.getItem('mock.sports') === 'matchups' } catch { return false } }

interface Network { ext: number; stored: string; shown: string; quality: SportsChannel['quality']; listing?: { title: string; teams: string[] } }
const networks: Network[] = [
  { ext: 31, stored: 'XX: CORVAN LEAGUE REPLAY 1 HD', shown: 'CORVAN LEAGUE REPLAY 1', quality: 'HD' },
  { ext: 32, stored: 'CORVAN LEAGUE ENDZONE ᴴᴰ', shown: 'CORVAN LEAGUE ENDZONE', quality: 'HD' },
  { ext: 33, stored: 'COMBAT HD', shown: 'COMBAT', quality: 'HD' },
  { ext: 34, stored: 'XX: Example Football Network ᶠᴴᴰ', shown: 'Example Football Network', quality: 'FHD', listing: { title: 'Football: Rivermen at Highlanders', teams: ['Rivermen', 'Highlanders'] } },
  { ext: 35, stored: 'CORVAN LEAGUE: PORT LINDEN KESTRELS ᴴᴰ', shown: 'CORVAN LEAGUE: PORT LINDEN KESTRELS', quality: 'HD' },
  { ext: 36, stored: 'Example Sports 2 ⁴ᴷ', shown: 'Example Sports 2', quality: '4K' },
]

const idOf = (ext: number) => `ch:s_xt:9${String(ext).padStart(3, '0')}`
const base = (ext: number, number: number, name: string, group: string, language = 'eng'): Channel => ({
  id: idOf(ext), source_id: 's_xt', ext_id: `9${String(ext).padStart(3, '0')}`, number, name, logo: logo(name), group, language,
  language_source: 'tag', country: null, epg_id: `sport${ext}`, catchup_days: 0, is_favorite: false,
})
/** The manager's filler: a channel without a guide "shows" its own name in four-hour blocks. */
const filler = (ext: number, stored: string): Programme => {
  const start = Math.floor(Date.now() / (4 * HOUR)) * 4 * HOUR
  return { id: `prog:${ext}${start / HOUR}`, channel_id: idOf(ext), start: new Date(start).toISOString(), stop: new Date(start + 4 * HOUR).toISOString(), title: stored, category: null, catchup_available: false, is_sport: true, teams: [] }
}
const isFiller = (p: Programme, stored: string) => p.title.replace(/\s+/g, ' ').trim().toLowerCase() === stored.replace(/\s+/g, ' ').trim().toLowerCase()
const gameOf = (s: Slot): Programme | null => s.start === null ? null : {
  id: `prog:slot-${s.ext}-${Math.floor(s.start / 1000)}`, channel_id: idOf(s.ext), start: new Date(s.start).toISOString(), stop: new Date(s.start + EVENT_LENGTH).toISOString(),
  title: s.title, category: s.sport, catchup_available: false, is_sport: true, teams: s.home && s.away ? (s.title.indexOf(s.home) < s.title.indexOf(s.away) ? [s.home, s.away] : [s.away, s.home]) : [],
}
const listingOf = (n: Network): Programme | undefined => n.listing && {
  id: `prog:${n.ext}`, channel_id: idOf(n.ext), start: new Date(BOOT - 25 * MIN).toISOString(), stop: new Date(BOOT + 95 * MIN).toISOString(),
  title: n.listing.title, category: 'Football', catchup_available: false, is_sport: true, teams: n.listing.teams,
}

/** Channels of the line-up as /channels would list them: shown names, the game of a slot, never filler. */
export const sportsChannels: Channel[] = [
  ...[...slots, ...matchupSlots].map((s) => base(s.ext, s.number, s.shown, 'Game day', s.language)),
  ...networks.map((n) => base(n.ext, 900 + n.ext, n.stored, 'Sports networks')),
]
/** Every guide entry a sports fixture can be recorded from, for the fixture recorder. */
export const sportsProgrammes = (): Programme[] => slots.map(gameOf).filter((p): p is Programme => !!p)

/** `serve` is told each game sent, the way the fixture recorder learns the guide entries it may be asked to record. */
export function sportsEvents(params: URLSearchParams, serve: (p: Programme) => unknown): components['schemas']['SportsEventsPage'] {
  const now = Date.now()
  const zone = params.get('timezone') ?? 'America/New_York'
  const date = params.get('date') ?? new Date(localDay(now) - new Date(now).getTimezoneOffset() * MIN).toISOString().slice(0, 10)
  const [y, m, d] = date.split('-').map(Number)
  const from = new Date(y!, m! - 1, d!).getTime()
  const to = from + Math.max(1, Math.min(7, Number(params.get('days') ?? 1))) * DAY
  const status = params.get('status') ?? 'all'
  const language = params.get('language')
  const languages = !language || language === 'all' ? null : language.split(',')
  const q = params.get('q')?.trim().toLowerCase() ?? ''
  const grouped = new Map<string, SportsEvent>()
  for (const s of withMatchups() ? [...slots, ...matchupSlots] : slots) {
    if (languages && !languages.includes(s.language)) continue
    // The slot's name is its schedule. Its guide rows repeat that name and are never read as listings.
    const live = s.start !== null && s.start <= now && now < s.start + EVENT_LENGTH
    if (s.start !== null && s.start + EVENT_LENGTH <= now) continue
    if (s.start === null ? !(from <= now && now < to) || !['all', 'active'].includes(status) : !(s.start + EVENT_LENGTH > from && s.start < to)) continue
    if ((status === 'current' && !live) || (status === 'upcoming' && (live || s.start === null)) || (status === 'replay' && !s.replay)) continue
    if (q && !`${s.title} ${s.shown} ${s.sport}`.toLowerCase().includes(q)) continue
    const game = gameOf(s)
    const id = `${s.title}|${s.sport}|${s.start ?? 'undated'}`
    const event = grouped.get(id) ?? {
      id: `ev${[...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7).toString(36)}`, kind: s.start === null ? 'channel' : 'event', title: s.title, sport: s.sport, league: s.league, home: s.home, away: s.away,
      start: new Date(s.start ?? from).toISOString(), stop: new Date(s.start === null ? to : s.start + EVENT_LENGTH).toISOString(),
      status: s.start === null ? 'unscheduled' : live ? 'current' : 'upcoming', replay: !!s.replay, live_evidence: live && !s.replay, grouping_evidence: 'matching_title_and_time', feeds: [],
      ...(s.matchup ? { matchup: s.matchup } : {}),
    }
    event.feeds.push({
      programme_id: game?.id ?? `prog:slot-${s.ext}-0`, channel_id: idOf(s.ext), channel_name: s.shown, channel_number: s.number, quality: s.quality,
      source_id: 's_xt', source_name: 'IPTV Provider', language: s.language, language_verified: false,
    })
    if (game) serve(game)
    grouped.set(id, event)
  }
  const all = [...grouped.values()].sort((a, b) => Number(a.kind !== 'event') - Number(b.kind !== 'event') || Date.parse(a.start) - Date.parse(b.start) || a.title.localeCompare(b.title))
  all.forEach((event) => event.feeds.sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0)))
  const counts = new Map<string, number>()
  all.forEach((event) => counts.set(event.sport, (counts.get(event.sport) ?? 0) + 1))
  const sport = params.get('sport')
  const events = all.filter((event) => !sport || event.sport === sport)
  const limit = Number(params.get('limit') ?? 40), offset = Number(params.get('offset') ?? 0)
  return {
    as_of: new Date(now).toISOString(), date, timezone: zone, events: events.slice(offset, offset + limit), total: events.length, limit, offset, truncated: false,
    sports: [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name)),
    coverage: { eligible_channels: 40 + sportsChannels.length, channels_with_sports_schedule: new Set(all.flatMap((event) => event.feeds.map((feed) => feed.channel_id))).size },
  }
}

export function sportsNetworks(params: URLSearchParams, serve: (p: Programme) => Programme): components['schemas']['SportsChannelsPage'] {
  const q = params.get('q')?.trim().toLowerCase() ?? ''
  const language = params.get('language')
  const listed = networks.filter((n) => (!q || n.stored.toLowerCase().includes(q)) && (!language || language === 'all' || language.split(',').includes('eng')))
  const limit = Number(params.get('limit') ?? 40), offset = Number(params.get('offset') ?? 0)
  return {
    channels: listed.slice(offset, offset + limit).map((n) => {
      // A network's guide is its real listing if it has one; filler is not sent.
      const on = listingOf(n) ?? filler(n.ext, n.stored)
      return { channel_id: idOf(n.ext), channel_name: n.shown, channel_number: 900 + n.ext, logo: logo(n.shown), group: 'Sports networks', quality: n.quality, language: 'eng', source_id: 's_xt', now: isFiller(on, n.stored) ? undefined : serve(on) }
    }),
    total: listed.length, limit, offset, truncated: false,
  }
}
