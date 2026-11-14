import type { Page } from '@playwright/test'

/** One feed of a stubbed event: `id` becomes the channel `ch:fixture:<id>`. */
export interface StubFeed { id: string; name?: string; number?: number; quality?: string | null; language?: string | null }
/** A stubbed /sports/events entry. Times are minutes from the page's clock at the time of the request. */
export interface StubEvent {
  id: string; title: string; sport: string; startMin: number; lengthMin?: number; feeds: StubFeed[]
  kind?: 'event' | 'channel'; league?: string | null; home?: string | null; away?: string | null; live?: boolean; replay?: boolean
}
export interface StubNetwork { id: string; name: string; number: number; quality?: string | null; now?: string | null }

/**
 * Answer /sports/events and /sports/channels from a fixed list, the way the server would: only what
 * is live or still to come, inside the asked days, narrowed by sport and search, counted per sport
 * before the sport filter. The last query string is kept in sessionStorage['sports.query'].
 * `window.holdSports = true` parks the next events request until `window.releaseSports()`.
 */
export async function stubSports(page: Page, events: StubEvent[], networks: StubNetwork[] = []) {
  await page.addInitScript(([events, networks]) => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname === '/api/v1/sports/channels') {
        const now = Date.now()
        return Response.json({ total: networks.length, limit: 24, offset: 0, truncated: false, channels: networks.map((n) => ({
          channel_id: `ch:fixture:${n.id}`, channel_name: n.name, channel_number: n.number, logo: null, group: 'Sports', quality: n.quality ?? null, language: 'eng', source_id: 'fixture',
          now: n.now ? { id: `prog:${n.id}`, channel_id: `ch:fixture:${n.id}`, start: new Date(now - 600000).toISOString(), stop: new Date(now + 3000000).toISOString(), title: n.now, catchup_available: false, is_sport: true, teams: [] } : null,
        })) })
      }
      if (url.pathname !== '/api/v1/sports/events') return original(request)
      const state = window as unknown as { holdSports?: boolean; sportsPending?: boolean; releaseSports?: () => void }
      if (state.holdSports && url.searchParams.get('status') !== 'upcoming') { state.holdSports = false; state.sportsPending = true; await new Promise<void>((resolve) => { state.releaseSports = resolve }) }
      // A sport row's own request (status=upcoming) is not the screen's query.
      if (url.searchParams.get('status') !== 'upcoming') sessionStorage.setItem('sports.query', url.search)
      const now = Date.now(), iso = (at: number) => new Date(at).toISOString()
      const [y, m, d] = (url.searchParams.get('date') ?? '').split('-').map(Number)
      const from = new Date(y!, m! - 1, d!).getTime(), to = from + Number(url.searchParams.get('days') ?? 1) * 86400000
      const q = url.searchParams.get('q')?.toLowerCase() ?? ''
      let listed = events.map((e) => {
        const start = now + e.startMin * 60000, stop = start + (e.lengthMin ?? 210) * 60000
        const channel = e.kind === 'channel'
        return {
          id: e.id, kind: e.kind ?? 'event', title: e.title, sport: e.sport, league: e.league ?? null, home: e.home ?? null, away: e.away ?? null,
          start: iso(channel ? from : start), stop: iso(channel ? to : stop), status: channel ? 'unscheduled' : start > now ? 'upcoming' : stop > now ? 'current' : 'ended',
          replay: !!e.replay, live_evidence: e.live ?? (start <= now && stop > now), grouping_evidence: 'matching_title_and_time',
          feeds: e.feeds.map((f) => ({ programme_id: `prog:slot-${f.id}-${Math.floor(start / 1000)}`, channel_id: `ch:fixture:${f.id}`, channel_name: f.name ?? `Channel ${f.id}`, channel_number: f.number ?? null, quality: f.quality ?? null, source_id: 'fixture', source_name: 'Fixture', language: f.language ?? null, language_verified: false })),
        }
      }).filter((e) => url.searchParams.get('status') !== 'upcoming' || e.status === 'upcoming').filter((e) => e.status !== 'ended' && (e.kind === 'channel' ? from <= now && now < to : Date.parse(e.stop) > from && Date.parse(e.start) < to))
        .filter((e) => !q || `${e.title} ${e.feeds.map((f) => f.channel_name).join(' ')}`.toLowerCase().includes(q))
        .sort((a, b) => Number(a.kind === 'channel') - Number(b.kind === 'channel') || Date.parse(a.start) - Date.parse(b.start))
      const counts = new Map<string, number>()
      listed.forEach((e) => counts.set(e.sport, (counts.get(e.sport) ?? 0) + 1))
      const sport = url.searchParams.get('sport')
      if (sport) listed = listed.filter((e) => e.sport === sport)
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 40)
      return Response.json({
        as_of: iso(now), date: url.searchParams.get('date'), timezone: url.searchParams.get('timezone'), events: listed.slice(offset, offset + limit), total: listed.length, offset, limit, truncated: false,
        sports: [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name)), coverage: { eligible_channels: 50, channels_with_sports_schedule: listed.length },
      })
    }
  }, [events, networks] as const)
}

/** The owner, signed in with the first-run notice accepted: the profile that may record. */
export async function asOwner(page: Page) {
  await page.addInitScript(() => {
    const profile = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2']))
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile }))
  })
}

export const listing = (page: Page, title: string) => page.getByTestId('sports-listing').filter({ hasText: title })
export const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && Array.from(document.querySelectorAll('.dialog')).every((d) => d.scrollWidth <= d.clientWidth))
