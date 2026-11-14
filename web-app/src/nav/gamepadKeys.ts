/**
 * Which remote keys a game controller is pressing, and when a held one repeats. Pure, so it is
 * unit-tested; gamepad.ts reads the controllers and sends the keys.
 */
export type PadKey = 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight' | 'Enter' | 'Escape'

/** What one controller is doing, read from a Gamepad (buttons pressed, stick positions). */
export interface PadSnapshot { pressed: readonly boolean[]; axes: readonly number[] }

// Indices in the W3C "standard" mapping, which Xbox controllers report.
const BUTTONS: readonly (readonly [number, PadKey])[] = [
  [12, 'ArrowUp'], [13, 'ArrowDown'], [14, 'ArrowLeft'], [15, 'ArrowRight'],
  [0, 'Enter'], [1, 'Escape'],
]
/** How far the stick must lean to count as a press; worn sticks rest well off zero. */
export const STICK_THRESHOLD = 0.6
/** A held direction repeats like a held remote key: once, a pause, then steadily. */
export const REPEAT_DELAY_MS = 400
export const REPEAT_EVERY_MS = 120

const ARROWS = new Set<PadKey>(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'])

/** Every key the controllers are holding right now. */
export function heldKeys(pads: readonly PadSnapshot[]): Set<PadKey> {
  const held = new Set<PadKey>()
  for (const pad of pads) {
    for (const [index, key] of BUTTONS) if (pad.pressed[index]) held.add(key)
    const [x = 0, y = 0] = pad.axes
    if (x <= -STICK_THRESHOLD) held.add('ArrowLeft')
    if (x >= STICK_THRESHOLD) held.add('ArrowRight')
    if (y <= -STICK_THRESHOLD) held.add('ArrowUp')
    if (y >= STICK_THRESHOLD) held.add('ArrowDown')
  }
  return held
}

/**
 * One poll: which keys go down and which come up. [repeatAt] carries each held key's next repeat
 * time between polls. Only arrows repeat; a held A or B is one press, as a held remote OK is.
 */
export function padStep(repeatAt: Map<PadKey, number>, held: ReadonlySet<PadKey>, now: number): { down: PadKey[]; up: PadKey[] } {
  const down: PadKey[] = [], up: PadKey[] = []
  for (const key of [...repeatAt.keys()]) {
    if (!held.has(key)) { repeatAt.delete(key); up.push(key) }
  }
  for (const key of held) {
    const at = repeatAt.get(key)
    if (at === undefined) {
      down.push(key)
      repeatAt.set(key, now + REPEAT_DELAY_MS)
    } else if (ARROWS.has(key) && now >= at) {
      down.push(key)
      repeatAt.set(key, now + REPEAT_EVERY_MS)
    }
  }
  return { down, up }
}
