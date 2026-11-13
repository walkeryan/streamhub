import type { RequestPhase, RequestState, Tile, TileRequestProgress } from '../api/types'

const OPEN: ReadonlySet<RequestState> = new Set(['pending_approval', 'approved', 'sent'])

/** An open request is still on its way; a closed one may be asked for again. */
export const isOpenRequest = (state: RequestState | null | undefined): boolean => !!state && OPEN.has(state)

export const PHASE_LABEL: Record<RequestPhase, string> = {
  requested: 'Requested',
  approved: 'Approved',
  searching: 'Searching',
  downloading: 'Downloading',
  importing: 'Importing',
  partially_available: 'Partly available',
  available: 'Available',
  declined: 'Declined',
  failed: 'Failed',
  stalled: 'Stalled',
  unknown: 'Status unknown',
}

/** Good news, something that needs a look, or neither. The label always says which: the tone only adds to it. */
export const phaseTone = (phase: RequestPhase): 'ok' | 'warn' | 'plain' =>
  phase === 'available' ? 'ok' : phase === 'failed' || phase === 'declined' || phase === 'stalled' ? 'warn' : 'plain'

/** What a server that sends no progress still tells: only what the request's state shows. */
const phaseOfState = (state: RequestState): RequestPhase => (state === 'pending_approval' ? 'requested' : state === 'sent' ? 'searching' : state)

type Requested = Pick<Tile, 'sources' | 'request_state' | 'request_progress'>
/** What the title's request is doing, while the title cannot be played here; null when it can, or was never asked for. */
export function requestOf(t: Requested): TileRequestProgress | null {
  if (!t.request_state || t.sources.some((s) => s.playable_in_app)) return null
  return t.request_progress ?? { phase: phaseOfState(t.request_state) }
}
/** The part of that a poster shows: a request on its way, or one that arrived before the library has the title. Not old refusals. */
export function tileRequest(t: Requested): TileRequestProgress | null {
  return isOpenRequest(t.request_state) || t.request_state === 'available' ? requestOf(t) : null
}

// How often a screen asks the server again while it shows a request. The server asks the request manager
// about a download every 15 s and about a search every few minutes, so asking it more often than this
// would show nothing new: 5 s keeps a bar within a few seconds of what the server knows.
const MOVING_MS = 5_000
const WAITING_MS = 15_000
const IDLE_MS = 60_000
const paceOf = (phase: RequestPhase): number =>
  phase === 'downloading' || phase === 'importing' ? MOVING_MS
    : phase === 'approved' || phase === 'searching' || phase === 'partially_available' || phase === 'stalled' || phase === 'unknown' ? WAITING_MS
      : IDLE_MS
/** The refetch interval for a view showing requests in these phases: that of the busiest one. */
export const refetchMsFor = (phases: Array<RequestPhase | null | undefined>): number =>
  phases.reduce((ms, phase) => (phase ? Math.min(ms, paceOf(phase)) : ms), IDLE_MS)

/**
 * The same for a page of posters (Home), which is a heavier thing to ask for: no faster than every 15 s, and
 * not at all unless one of its posters shows a request.
 */
export function tilesRefetchMs(tiles: Requested[]): number | false {
  const phases = tiles.map((t) => tileRequest(t)?.phase).filter((phase) => phase && phase !== 'available')
  return phases.length ? Math.max(WAITING_MS, refetchMsFor(phases)) : false
}

/** Requests screen order: what is moving first, then what is waiting, then what is over. Ties keep the server's order. */
const RANK: Record<RequestPhase, number> = {
  downloading: 0, importing: 0, stalled: 1,
  searching: 2, approved: 2, requested: 2, partially_available: 2, unknown: 2,
  available: 3, declined: 3, failed: 3,
}
export const byActivity = <T extends { progress: { phase: RequestPhase } }>(rows: T[]): T[] =>
  rows.map((row, i) => ({ row, i })).sort((a, b) => RANK[a.row.progress.phase] - RANK[b.row.progress.phase] || a.i - b.i).map((x) => x.row)

/**
 * "about 12 min left"; null when there is no time, or it has passed. Counted from when the server last
 * checked, not from this device's clock: the two times come from one answer, so a clock that is off here
 * cannot make the wait look longer or shorter, and the answer is never more than a few seconds old.
 */
export function etaText(p: { eta?: string | null; checked_at?: string | null }): string | null {
  const left = p.eta && p.checked_at ? Date.parse(p.eta) - Date.parse(p.checked_at) : NaN
  if (!Number.isFinite(left) || left <= 0) return null
  const minutes = Math.round(left / 60_000)
  if (minutes < 1) return 'less than a minute left'
  if (minutes < 60) return `about ${minutes} min left`
  const hours = Math.floor(minutes / 60), rest = minutes % 60
  return `about ${hours} hr${rest ? ` ${rest} min` : ''} left`
}

export function tmdbRef(id: string): { media_type: 'movie' | 'tv'; tmdb_id: number } | null {
  const m = /^tmdb:(movie|tv):(\d+)$/.exec(id)
  return m ? { media_type: m[1] as 'movie' | 'tv', tmdb_id: Number(m[2]) } : null
}
