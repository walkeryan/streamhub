/**
 * Steering a member's player to the room clock. The room state is `position_ms` at server time
 * `as_of_ms`; while playing the position moves with the clock. A member more than DRIFT_LIMIT_MS from
 * the room position seeks to it; smaller errors are left alone (no speed nudging).
 */
export const DRIFT_LIMIT_MS = 1000

export interface RoomClockState {
  playing: boolean
  /** null in a live room: a channel has no position */
  position_ms: number | null
  as_of_ms: number
}

export type SyncAction = { type: 'none' } | { type: 'play' } | { type: 'pause' } | { type: 'seek'; positionMs: number }

/** Where the room is at server time `serverNowMs`; null for a live room. `durationMs` caps a clock nobody stopped. */
export function positionAt(state: RoomClockState, serverNowMs: number, durationMs?: number | null): number | null {
  if (state.position_ms === null) return null
  const moved = state.playing ? Math.max(0, serverNowMs - state.as_of_ms) : 0
  const at = Math.max(0, state.position_ms + moved)
  return durationMs && durationMs > 0 ? Math.min(at, durationMs) : at
}

/**
 * The one thing this member's player should do now. Called on every room event and on a short timer,
 * so each call makes a single correction: stop first when the room is paused, then get to the right
 * place, then start.
 */
export function decide(state: RoomClockState, serverNowMs: number, localPositionMs: number, localPlaying: boolean, durationMs?: number | null): SyncAction {
  if (!state.playing && localPlaying) return { type: 'pause' }
  const target = positionAt(state, serverNowMs, durationMs)
  if (target !== null && Math.abs(localPositionMs - target) > DRIFT_LIMIT_MS) return { type: 'seek', positionMs: Math.round(target) }
  if (state.playing && !localPlaying) return { type: 'play' }
  return { type: 'none' }
}

/**
 * Did someone seek between two room states? Play and pause keep the position the clock already had;
 * a seek puts it somewhere else. The stream of a room may be restarted on a seek, so this is the
 * moment a member's descriptor can go stale.
 */
export function isSeek(previous: RoomClockState | null, next: RoomClockState): boolean {
  if (!previous || next.position_ms === null) return false
  const expected = positionAt(previous, next.as_of_ms)
  return expected === null || Math.abs(expected - next.position_ms) > DRIFT_LIMIT_MS
}
