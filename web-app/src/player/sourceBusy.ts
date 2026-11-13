import type { ActiveStream } from '../api/types'

/** A `source_busy` answer: who holds the source's connections, and how many it allows. `own` marks the
 *  other refusal with the same shape, `user_stream_limit`: the streams are the viewer's own. */
export interface SourceBusy { active: ActiveStream[]; limit: number | null; own?: boolean }

/** The body of a 409 from resolve or from the stream itself; null unless it says source_busy or user_stream_limit. */
export function parseSourceBusy(body: unknown): SourceBusy | null {
  const code = typeof body === 'object' && body !== null ? (body as { code?: unknown }).code : undefined
  if (code !== 'source_busy' && code !== 'user_stream_limit') return null
  const { active, limit } = body as { active?: unknown; limit?: unknown }
  return {
    active: Array.isArray(active) ? active.filter((a): a is ActiveStream => typeof a === 'object' && a !== null) : [],
    limit: typeof limit === 'number' && limit > 0 ? limit : null,
    ...(code === 'user_stream_limit' ? { own: true } : {}),
  }
}

export function busyHeading(busy: SourceBusy): string {
  if (busy.own) {
    const most = busy.limit ?? busy.active.length
    return most === 1 ? 'You are already watching something' : most > 1 ? `You are already watching ${most} streams` : 'You have reached your stream limit'
  }
  // Without the limit, two named streams is still a pair; anything else is not worth a count.
  const count = busy.limit ?? (busy.active.length === 2 ? 2 : null)
  if (count === 1) return 'The stream is in use'
  if (count === 2) return 'Both streams are in use'
  return count ? `All ${count} streams are in use` : 'No free stream'
}

/** One line per active stream: "<name> is watching <title>". */
export function busyLines(busy: SourceBusy): string[] {
  if (busy.own) return busy.active.map((a) => `${a.title?.trim() || 'Another title'} is playing on another device`)
  return busy.active.map((a) => `${a.profile_name?.trim() || 'Someone'} is watching ${a.title?.trim() || 'something else'}`)
}

/** The 401 codes that are about the session a stream URL or a room seat was issued to. */
export const SESSION_CODES: ReadonlySet<string> = new Set(['session_revoked', 'user_suspended', 'session_stale'])
export const AWAY_FROM_HOME = 'This account can only be used on the home network. Connect to it, or ask the person who runs this StreamHub to allow watching away from home.'

/**
 * What one of our stream URLs said when it would not play. `busy`: the source or the viewer's own
 * limit (409). `denied`: it will not play for this user here, and asking again changes nothing (403).
 * `session`: the session the URL was issued to no longer stands (401), which the API has to confirm.
 */
export type StreamRefusal = { busy: SourceBusy } | { denied: string } | { session: true }
export function streamRefusal(body: unknown): StreamRefusal | null {
  const busy = parseSourceBusy(body)
  if (busy) return { busy }
  const { code, message } = (typeof body === 'object' && body !== null ? body : {}) as { code?: unknown; message?: unknown }
  if (code === 'remote_access_denied') return { denied: AWAY_FROM_HOME }
  if (code === 'content_restricted') return { denied: typeof message === 'string' && message ? message : 'This is no longer available to this account.' }
  return typeof code === 'string' && SESSION_CODES.has(code) ? { session: true } : null
}
