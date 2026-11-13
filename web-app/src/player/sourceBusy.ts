import type { ActiveStream } from '../api/types'

/** A `source_busy` answer: who holds the source's connections, and how many it allows. */
export interface SourceBusy { active: ActiveStream[]; limit: number | null }

/** The body of a 409 from resolve or from the stream itself; null unless it says source_busy. */
export function parseSourceBusy(body: unknown): SourceBusy | null {
  if (typeof body !== 'object' || body === null || (body as { code?: unknown }).code !== 'source_busy') return null
  const { active, limit } = body as { active?: unknown; limit?: unknown }
  return {
    active: Array.isArray(active) ? active.filter((a): a is ActiveStream => typeof a === 'object' && a !== null) : [],
    limit: typeof limit === 'number' && limit > 0 ? limit : null,
  }
}

export function busyHeading(busy: SourceBusy): string {
  // Without the limit, two named streams is still a pair; anything else is not worth a count.
  const count = busy.limit ?? (busy.active.length === 2 ? 2 : null)
  if (count === 1) return 'The stream is in use'
  if (count === 2) return 'Both streams are in use'
  return count ? `All ${count} streams are in use` : 'No free stream'
}

/** One line per active stream: "<name> is watching <title>". */
export function busyLines(busy: SourceBusy): string[] {
  return busy.active.map((a) => `${a.profile_name?.trim() || 'Someone'} is watching ${a.title?.trim() || 'something else'}`)
}
