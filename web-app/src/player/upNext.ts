/*
 * Skip intro / Skip credits, Up next and Still watching: the rules, with no DOM or React in them, so
 * they are unit-tested on their own (tests/up-next.test.mjs). Player.tsx feeds them positions and
 * events and shows what they decide. Keep this file free of imports. The TV app follows the same rules
 * (docs/specs/2026-11-18-title-and-player-design.md, "Behaviour").
 */

export interface Marker { type: 'intro' | 'credits'; start_ms: number; end_ms: number }

/** The card counts this long before the next episode plays by itself. */
export const COUNTDOWN_MS = 10_000
/** Without a credits marker the card comes in this long before the end. */
export const NO_CREDITS_LEAD_MS = 30_000
/** This many episodes in a row started by autoplay, with no input between them, and the next one waits. */
export const STILL_WATCHING_AFTER = 3

/** The intro marker the position is inside, if any: while it lasts, Skip intro is offered. */
export function introAt(markers: readonly Marker[] | null | undefined, positionMs: number): Marker | null {
  return (markers ?? []).find((m) => m.type === 'intro' && positionMs >= m.start_ms && positionMs < m.end_ms - 500) ?? null
}

/** The last credits marker: where Skip credits and the Up next card begin. */
export function lastCredits(markers: readonly Marker[] | null | undefined): Marker | null {
  const credits = (markers ?? []).filter((m) => m.type === 'credits')
  return credits.length ? credits.reduce((a, b) => (b.start_ms >= a.start_ms ? b : a)) : null
}

/** A credits marker the position is inside (a film's Skip credits, an episode's own end titles). */
export function creditsAt(markers: readonly Marker[] | null | undefined, positionMs: number): Marker | null {
  const last = lastCredits(markers)
  return last && positionMs >= last.start_ms && positionMs < last.end_ms ? last : null
}

/**
 * Where the Up next card comes in for an episode that has a next one: the start of its last credits
 * marker, else NO_CREDITS_LEAD_MS before the end. Null when it cannot be known (no length yet).
 */
export function upNextStart(markers: readonly Marker[] | null | undefined, durationMs: number | null | undefined): number | null {
  const credits = lastCredits(markers)
  if (credits) return credits.start_ms
  if (!durationMs || durationMs <= 0) return null
  // A very short title (a clip, a test file) gets the card in its last third, not from its first second.
  return Math.max(durationMs - NO_CREDITS_LEAD_MS, Math.floor((durationMs * 2) / 3))
}

/**
 * The Up next card.
 *  hidden   not shown (before its start, or dismissed until the end)
 *  credits  shown over the credits, counting down while the picture plays
 *  end      shown after the episode ended, with a fresh countdown (it had been dismissed)
 * `fire` turns true when the countdown reaches zero with autoplay on: the player then moves on and resets.
 */
export interface UpNextState {
  phase: 'hidden' | 'credits' | 'end'
  dismissed: boolean
  remainingMs: number
  fire: boolean
}

export type UpNextEvent =
  | { type: 'position'; positionMs: number; startMs: number | null }
  | { type: 'tick'; ms: number; playing: boolean; autoplay: boolean }
  | { type: 'ended'; autoplay: boolean }
  /** Back, Escape, "Keep watching". */
  | { type: 'dismiss' }
  /** Any seek. `toMs` is where it landed; before the card's start, the card may come again there. */
  | { type: 'seek'; toMs: number; startMs: number | null }
  | { type: 'reset' }

export const UP_NEXT_IDLE: UpNextState = { phase: 'hidden', dismissed: false, remainingMs: COUNTDOWN_MS, fire: false }

export function upNextReducer(state: UpNextState, event: UpNextEvent): UpNextState {
  switch (event.type) {
    case 'reset':
      return UP_NEXT_IDLE
    case 'position': {
      if (state.phase === 'end' || state.fire) return state
      const inside = event.startMs !== null && event.positionMs >= event.startMs
      if (state.phase === 'hidden' && inside && !state.dismissed) return { ...state, phase: 'credits', remainingMs: COUNTDOWN_MS }
      if (state.phase === 'credits' && !inside) return { ...state, phase: 'hidden', remainingMs: COUNTDOWN_MS }
      return state
    }
    case 'tick': {
      if (state.fire || !event.autoplay || state.phase === 'hidden') return state
      // Over the credits the count follows the picture: paused, it waits. After the end there is no picture to follow.
      if (state.phase === 'credits' && !event.playing) return state
      const remainingMs = Math.max(0, state.remainingMs - Math.max(0, event.ms))
      return { ...state, remainingMs, fire: remainingMs === 0 }
    }
    case 'ended':
      if (state.fire) return state
      // Still counting over the credits when the picture ran out: nothing is left to wait for.
      if (state.phase === 'credits' && event.autoplay) return { ...state, remainingMs: 0, fire: true }
      if (state.phase === 'credits') return state
      return { phase: 'end', dismissed: true, remainingMs: COUNTDOWN_MS, fire: false }
    case 'dismiss':
      return state.phase === 'hidden' ? { ...state, dismissed: true } : { ...state, phase: 'hidden', dismissed: true, remainingMs: COUNTDOWN_MS, fire: false }
    case 'seek': {
      if (state.phase === 'end') {
        // Seeking back from the end: the picture plays again, and the card waits for the end once more.
        return { phase: 'hidden', dismissed: true, remainingMs: COUNTDOWN_MS, fire: false }
      }
      if (state.phase === 'credits') return { ...state, phase: 'hidden', dismissed: true, remainingMs: COUNTDOWN_MS }
      // Back before the card's start: watching that part again, the card comes in at its start as before.
      if (event.startMs !== null && event.toMs < event.startMs) return { ...state, dismissed: false }
      return state
    }
  }
}

/** Seconds shown in the ring: 10 … 1 (never 0, which is when it plays). */
export const countdownSeconds = (remainingMs: number): number => Math.max(1, Math.ceil(remainingMs / 1000))

/**
 * Still watching? `streak` counts episodes in a row started by autoplay with no key press, click or
 * touch since. Input sets it to 0; each autoplay that goes ahead adds one. When an autoplay is due and
 * the streak has reached STILL_WATCHING_AFTER, the player pauses on the prompt instead.
 */
export const streakAfterInput = (): number => 0
export const streakAfterAutoplay = (streak: number): number => streak + 1
export const asksStillWatching = (streak: number): boolean => streak >= STILL_WATCHING_AFTER

/** "S2 · E3 · The Storm" style line under a mini player's title, and the player's top line. */
export function episodeLine(show: string | null | undefined, season: number | null | undefined, episode: number | null | undefined): string {
  const parts: string[] = []
  if (show) parts.push(show)
  if (season != null) parts.push(`Season ${season}`)
  if (episode != null) parts.push(`Episode ${episode}`)
  return parts.join(' · ')
}

export function episodeCode(season: number | null | undefined, episode: number | null | undefined): string {
  return [season != null ? `S${season}` : '', episode != null ? `E${episode}` : ''].filter(Boolean).join(' · ')
}

/** A preview frame's position: rounded down to the index's spacing, so nearby positions share one URL. */
export function thumbnailAt(url: string, intervalMs: number, positionMs: number): string {
  const step = intervalMs > 0 ? intervalMs : 2000
  const ms = Math.max(0, Math.floor(positionMs / step) * step)
  return `${url}${url.includes('?') ? '&' : '?'}ms=${ms}`
}
