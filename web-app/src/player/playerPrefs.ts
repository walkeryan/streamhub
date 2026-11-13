// The explicit extension lets the node tests import this module without a bundler.
import { lsGet, lsSet } from '../lib/util.ts'

/** What a viewer set on this browser's player. Kept per browser, not per profile: it describes the room and its speakers. */
export interface PlayerPrefs {
  /** 0 to 1. */
  volume: number
  muted: boolean
  speed: number
  /** The time readout counts down what is left instead of showing the title's length. */
  remaining: boolean
  /** Label of the subtitle track last switched on: what C brings back. */
  subtitle: string | null
}

const KEY = 'sh.player.v1'
export const SPEED_MIN = 0.5, SPEED_MAX = 2, SPEED_STEP = 0.25
/** 0.5× to 2× in quarters. */
export const SPEEDS: readonly number[] = Array.from({ length: (SPEED_MAX - SPEED_MIN) / SPEED_STEP + 1 }, (_, i) => SPEED_MIN + i * SPEED_STEP)
export const VOLUME_STEP = 0.05
export const DEFAULT_PREFS: PlayerPrefs = { volume: 1, muted: false, speed: 1, remaining: false, subtitle: null }

const unit = (n: unknown, fallback: number): number => (typeof n === 'number' && Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : fallback)
/** The nearest speed the player offers; anything unreadable is normal speed. */
export function clampSpeed(n: unknown): number {
  if (typeof n !== 'number' || !Number.isFinite(n)) return 1
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(n / SPEED_STEP) * SPEED_STEP))
}
export const nextSpeed = (speed: number, dir: -1 | 1): number => clampSpeed(clampSpeed(speed) + dir * SPEED_STEP)
export const speedLabel = (speed: number): string => (speed === 1 ? 'Normal' : `${speed}×`)
/** Volume moves in twentieths and lands on them, so ten presses down from full is exactly half. */
export const nextVolume = (volume: number, dir: -1 | 1): number => Math.min(1, Math.max(0, Math.round((volume + dir * VOLUME_STEP) * 20) / 20))

export function parsePrefs(raw: string | null): PlayerPrefs {
  let found: Partial<Record<keyof PlayerPrefs, unknown>> = {}
  try { const parsed: unknown = raw ? JSON.parse(raw) : null; if (parsed && typeof parsed === 'object') found = parsed } catch { /* an unreadable value is no value */ }
  return { volume: unit(found.volume, DEFAULT_PREFS.volume), muted: found.muted === true, speed: clampSpeed(found.speed), remaining: found.remaining === true, subtitle: typeof found.subtitle === 'string' ? found.subtitle.slice(0, 80) : null }
}

// lsGet/lsSet swallow a missing or blocked localStorage: the player then simply forgets.
export const loadPrefs = (): PlayerPrefs => parsePrefs(lsGet(KEY))
export const savePrefs = (prefs: PlayerPrefs): void => lsSet(KEY, JSON.stringify(prefs))
