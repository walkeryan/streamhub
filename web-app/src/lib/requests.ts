import type { RequestState } from '../api/types'

const OPEN: ReadonlySet<RequestState> = new Set(['pending_approval', 'approved', 'sent'])

/** An open request is still on its way; a closed one may be asked for again. */
export const isOpenRequest = (state: RequestState | null | undefined): boolean => !!state && OPEN.has(state)

const LABEL: Record<RequestState, string> = {
  pending_approval: 'Requested · waiting for approval',
  approved: 'Approved · downloading',
  sent: 'Approved · downloading',
  available: 'Available',
  declined: 'Declined',
  failed: 'Request failed',
}
export const requestStateLabel = (state: RequestState): string => LABEL[state]

/** Short form for a poster badge; only while a request is open. */
export const requestBadge = (state: RequestState | null | undefined): string | null =>
  state === 'pending_approval' ? 'Requested' : isOpenRequest(state) ? 'On its way' : null

export function tmdbRef(id: string): { media_type: 'movie' | 'tv'; tmdb_id: number } | null {
  const m = /^tmdb:(movie|tv):(\d+)$/.exec(id)
  return m ? { media_type: m[1] as 'movie' | 'tv', tmdb_id: Number(m[2]) } : null
}
