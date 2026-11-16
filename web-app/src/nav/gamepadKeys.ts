/**
 * Which keys a game controller is pressing, and when a held one repeats. Pure, so it is unit-tested;
 * gamepad.ts reads the controllers and sends the keys.
 *
 * Every button becomes a key the app already answers from a keyboard or a remote, so the controller
 * has no meaning of its own past this file: the D-pad and A/B are a remote's arrows, OK and Back; the
 * rest are keys the player knows (shortcuts.ts) and every other screen ignores.
 */
export type PadKey =
  | 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight' | 'Enter' | 'Escape'
  | 'MediaPlayPause' | 'Info' | 'ContextMenu'
  | 'PageUp' | 'PageDown' | 'Shift+PageUp' | 'Shift+PageDown'
  | 'AudioVolumeUp' | 'AudioVolumeDown'

/**
 * What one controller is doing, read from a Gamepad (buttons pressed, stick positions). [values] are
 * the buttons' analog positions, which only the triggers really have.
 */
export interface PadSnapshot { pressed: readonly boolean[]; values?: readonly number[]; axes: readonly number[] }

// Indices in the W3C "standard" mapping, which Xbox controllers report.
export const PAD = { A: 0, B: 1, X: 2, Y: 3, LB: 4, RB: 5, LT: 6, RT: 7, VIEW: 8, START: 9, UP: 12, DOWN: 13, LEFT: 14, RIGHT: 15 } as const

const BUTTONS: readonly (readonly [number, PadKey])[] = [
  [PAD.UP, 'ArrowUp'], [PAD.DOWN, 'ArrowDown'], [PAD.LEFT, 'ArrowLeft'], [PAD.RIGHT, 'ArrowRight'],
  [PAD.A, 'Enter'], [PAD.B, 'Escape'],
  // View is the console's own Back button, so it is Back here too.
  [PAD.VIEW, 'Escape'],
  [PAD.X, 'MediaPlayPause'], [PAD.Y, 'Info'], [PAD.START, 'ContextMenu'],
  // The bumpers are channel down/up on live TV and a 10 s step in a film: Page Down/Up on a keyboard.
  [PAD.LB, 'PageDown'], [PAD.RB, 'PageUp'],
]
// The triggers are the bigger step, as Shift is for the keyboard's Page keys.
const TRIGGERS: readonly (readonly [number, PadKey])[] = [[PAD.LT, 'Shift+PageDown'], [PAD.RT, 'Shift+PageUp']]

/** How far the stick must lean to count as a press; worn sticks rest well off zero. */
export const STICK_THRESHOLD = 0.6
/**
 * How far a trigger must be pulled to count as a press. Browsers call a trigger pressed at the first
 * millimetre, which a resting finger reaches; half way is a deliberate pull.
 */
export const TRIGGER_THRESHOLD = 0.5
/** A held direction repeats like a held remote key: once, a pause, then steadily. */
export const REPEAT_DELAY_MS = 400
export const REPEAT_EVERY_MS = 120

/**
 * What repeats while held: moving, stepping and turning the volume. A, B, X, Y, View and Start are one
 * press however long they are held, as a held remote OK is: a toggle that repeated would flicker.
 */
const REPEATING = new Set<PadKey>([
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'PageUp', 'PageDown', 'Shift+PageUp', 'Shift+PageDown', 'AudioVolumeUp', 'AudioVolumeDown',
])

/**
 * The right stick's keys. The player turns them into volume; anywhere else nobody answers them and
 * gamepad.ts scrolls the page instead, this way (-1 up, 1 down).
 */
export const SCROLL_KEYS: ReadonlyMap<PadKey, -1 | 1> = new Map<PadKey, -1 | 1>([['AudioVolumeUp', -1], ['AudioVolumeDown', 1]])

/** The KeyboardEvent a pad key is sent as. */
export function keyEventInit(key: PadKey): { key: string; shiftKey: boolean } {
  return key.startsWith('Shift+') ? { key: key.slice('Shift+'.length), shiftKey: true } : { key, shiftKey: false }
}

/** Every key the controllers are holding right now. */
export function heldKeys(pads: readonly PadSnapshot[]): Set<PadKey> {
  const held = new Set<PadKey>()
  for (const pad of pads) {
    for (const [index, key] of BUTTONS) if (pad.pressed[index]) held.add(key)
    for (const [index, key] of TRIGGERS) {
      const value = pad.values?.[index] ?? (pad.pressed[index] ? 1 : 0)
      if (value >= TRIGGER_THRESHOLD) held.add(key)
    }
    const [x = 0, y = 0, , ry = 0] = pad.axes
    if (x <= -STICK_THRESHOLD) held.add('ArrowLeft')
    if (x >= STICK_THRESHOLD) held.add('ArrowRight')
    if (y <= -STICK_THRESHOLD) held.add('ArrowUp')
    if (y >= STICK_THRESHOLD) held.add('ArrowDown')
    // The right stick's up and down only: sideways has nothing to scroll or turn.
    if (ry <= -STICK_THRESHOLD) held.add('AudioVolumeUp')
    if (ry >= STICK_THRESHOLD) held.add('AudioVolumeDown')
  }
  return held
}

/**
 * One poll: which keys go down and which come up. [repeatAt] carries each held key's next repeat
 * time between polls. Only the REPEATING keys repeat.
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
    } else if (REPEATING.has(key) && now >= at) {
      down.push(key)
      repeatAt.set(key, now + REPEAT_EVERY_MS)
    }
  }
  return { down, up }
}
