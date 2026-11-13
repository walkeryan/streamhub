/**
 * Seek presses that become one seek.
 *
 * Many films play as restart-at-offset streams (see restartSeek.ts): a seek outside what the server has
 * produced starts a new transcoder and takes seconds. So Left/Right, J/L, the ±10 s buttons and a double
 * tap never seek by themselves. They move a pending target, shown on the scrubber, and the player commits
 * it once when the presses pause (COMMIT_MS) or on Enter.
 */

/** How long after the last press the pending target is committed. */
export const COMMIT_MS = 500
/** A held key repeats far faster than anyone can follow: it moves the target this often at most. */
export const HOLD_STEP_MS = 100
/** Presses closer together than this, kept up for HOLD_AFTER_MS, are a held key: some remotes repeat without saying so. */
export const HOLD_GAP_MS = 150
export const HOLD_AFTER_MS = 400
export const STEP_S = 10
export const BIG_STEP_S = 60

export interface PendingSeek {
  /** Position in the title (seconds) the presses started from. */
  base: number
  target: number
  dir: -1 | 1
  /** When the current run of quick or repeated presses began; null between separate presses. */
  heldSince: number | null
  /** When the target last moved, and when a press last arrived (a held key presses more often than it moves). */
  lastStepAt: number
  lastPressAt: number
}

export interface SeekPress {
  dir: -1 | 1
  /** Monotonic milliseconds. */
  now: number
  /** The keyboard says this is an auto-repeat. */
  held?: boolean
  /** Shift: a minute at a time. */
  big?: boolean
}

/** Steps grow while a key is held: 10 s for the first second, 30 s for the next two, then a minute. */
export function holdStep(heldMs: number): number {
  return heldMs < 1000 ? STEP_S : heldMs < 3000 ? 30 : BIG_STEP_S
}

/** Never onto the last second: a stream that starts at the end has nothing to play. */
export function clampTarget(target: number, duration: number): number {
  return Math.max(0, duration > 0 ? Math.min(target, Math.max(0, duration - 1)) : target)
}

/**
 * The pending target after one more press. Every separate press counts, however fast it follows the
 * last; a held key moves the target every HOLD_STEP_MS at most, by steps that grow.
 */
export function accumulate(pending: PendingSeek | null, press: SeekPress, position: number, duration: number): PendingSeek {
  const sameWay = pending !== null && pending.dir === press.dir
  const quick = sameWay && press.now - pending.lastPressAt < HOLD_GAP_MS
  const heldSince = press.held || quick ? (sameWay ? pending.heldSince ?? pending.lastPressAt : press.now) : null
  // The keyboard's own repeat flag says "held" at once; a run of quick presses has to last before it is believed.
  const held = heldSince !== null && (!!press.held || press.now - heldSince >= HOLD_AFTER_MS)
  if (pending && sameWay && held && press.now - pending.lastStepAt < HOLD_STEP_MS) return { ...pending, heldSince, lastPressAt: press.now }
  const step = press.big ? BIG_STEP_S : held ? holdStep(press.now - heldSince) : STEP_S
  const from = pending ? pending.target : position
  return { base: pending ? pending.base : position, target: clampTarget(from + press.dir * step, duration), dir: press.dir, heldSince, lastStepAt: press.now, lastPressAt: press.now }
}

/** A jump to a place (a number key, Home, End) waits like a step does, so several of them cost one seek. */
export function jump(pending: PendingSeek | null, target: number, position: number, duration: number, now: number): PendingSeek {
  const to = clampTarget(target, duration)
  const base = pending ? pending.base : position
  return { base, target: to, dir: to < base ? -1 : 1, heldSince: null, lastStepAt: now, lastPressAt: now }
}

/** 0 to 9 are tenths of the title. */
export const fractionTarget = (digit: number, duration: number): number => (duration > 0 ? (duration * digit) / 10 : 0)
/** End goes near the end, not onto it: what is left to watch there is the last ten seconds. */
export const endTarget = (duration: number): number => Math.max(0, duration - 10)

/** "+40 s", "−1 min 30 s", "+1 h 5 min": how far the pending target is from where the presses began. */
export function seekHint(deltaS: number): string {
  const total = Math.round(Math.abs(deltaS))
  const sign = deltaS < 0 ? '−' : '+'
  if (total < 60) return `${sign}${total} s`
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60
  if (h) return `${sign}${h} h${m ? ` ${m} min` : ''}`
  return `${sign}${m} min${s ? ` ${s} s` : ''}`
}
