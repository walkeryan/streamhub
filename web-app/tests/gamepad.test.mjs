import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PAD, REPEAT_DELAY_MS, REPEAT_EVERY_MS, SCROLL_KEYS, STICK_THRESHOLD, TRIGGER_THRESHOLD, heldKeys, keyEventInit, padStep } from '../src/nav/gamepadKeys.ts'

const pad = (buttons = [], axes = [0, 0, 0, 0], values) => {
  const pressed = Array(17).fill(false)
  for (const b of buttons) pressed[b] = true
  return values ? { pressed, values, axes } : { pressed, axes }
}
const held = (...pads) => [...heldKeys(pads)].sort()

test('D-pad, A and B are the remote keys; the left stick is the arrows past its threshold', () => {
  assert.deepEqual(held(pad([12])), ['ArrowUp'])
  assert.deepEqual(held(pad([13, 0])), ['ArrowDown', 'Enter'])
  assert.deepEqual(held(pad([1])), ['Escape'])
  assert.deepEqual(held(pad([], [STICK_THRESHOLD, 0])), ['ArrowRight'])
  assert.deepEqual(held(pad([], [0, -0.9])), ['ArrowUp'])
  // Sticks resting a little off centre, and the right stick sideways, do nothing.
  assert.deepEqual(held(pad([], [0.3, -0.2, 1, 0.2])), [])
  // Two controllers: either one moves focus.
  assert.deepEqual(held(pad([14]), pad([15])), ['ArrowLeft', 'ArrowRight'])
})

test('X, Y, Start and View are the player keys play/pause, info, menu and Back', () => {
  assert.deepEqual(held(pad([PAD.X])), ['MediaPlayPause'])
  assert.deepEqual(held(pad([PAD.Y])), ['Info'])
  assert.deepEqual(held(pad([PAD.START])), ['ContextMenu'])
  // View is Back, like B; both held is still one Back.
  assert.deepEqual(held(pad([PAD.VIEW])), ['Escape'])
  assert.deepEqual(held(pad([PAD.VIEW, PAD.B])), ['Escape'])
  // The guide button (16) and the stick clicks (10, 11) stay the console's.
  assert.deepEqual(held(pad([10, 11, 16])), [])
})

test('the bumpers are Page Down/Up, the triggers Shift+Page Down/Up once pulled past half way', () => {
  assert.deepEqual(held(pad([PAD.LB])), ['PageDown'])
  assert.deepEqual(held(pad([PAD.RB])), ['PageUp'])
  const trigger = (index, value) => {
    const values = Array(17).fill(0)
    values[index] = value
    // Browsers call a trigger pressed at its first millimetre: only the value decides here.
    return pad([index], [0, 0, 0, 0], values)
  }
  assert.deepEqual(held(trigger(PAD.LT, 0.2)), [])
  assert.deepEqual(held(trigger(PAD.LT, TRIGGER_THRESHOLD)), ['Shift+PageDown'])
  assert.deepEqual(held(trigger(PAD.RT, 1)), ['Shift+PageUp'])
  assert.deepEqual(held(trigger(PAD.RT, TRIGGER_THRESHOLD - 0.01)), [])
  // A snapshot without values (a digital trigger) goes by pressed.
  assert.deepEqual(held(pad([PAD.RT])), ['Shift+PageUp'])
  assert.deepEqual(keyEventInit('Shift+PageUp'), { key: 'PageUp', shiftKey: true })
  assert.deepEqual(keyEventInit('PageDown'), { key: 'PageDown', shiftKey: false })
  assert.deepEqual(keyEventInit('MediaPlayPause'), { key: 'MediaPlayPause', shiftKey: false })
})

test('the right stick up and down is volume up and down, and a page scroll where nothing takes it', () => {
  assert.deepEqual(held(pad([], [0, 0, 0, -STICK_THRESHOLD])), ['AudioVolumeUp'])
  assert.deepEqual(held(pad([], [0, 0, 0, 0.95])), ['AudioVolumeDown'])
  assert.deepEqual(held(pad([], [0, 0, 0, 0.4])), [])
  // Both sticks at once: the left one moves, the right one turns.
  assert.deepEqual(held(pad([], [0, 1, 0, -1])), ['ArrowDown', 'AudioVolumeUp'])
  assert.equal(SCROLL_KEYS.get('AudioVolumeUp'), -1)
  assert.equal(SCROLL_KEYS.get('AudioVolumeDown'), 1)
  assert.equal(SCROLL_KEYS.get('ArrowDown'), undefined)
})

test('a held arrow repeats after a pause; a held A is a single press; letting go sends key up', () => {
  const at = new Map()
  const down = new Set(['ArrowDown', 'Enter'])
  assert.deepEqual(padStep(at, down, 0), { down: ['ArrowDown', 'Enter'], up: [] })
  // Held through the pause: nothing more.
  assert.deepEqual(padStep(at, down, REPEAT_DELAY_MS - 1), { down: [], up: [] })
  // Then the arrow repeats, steadily; Enter never does.
  assert.deepEqual(padStep(at, down, REPEAT_DELAY_MS), { down: ['ArrowDown'], up: [] })
  assert.deepEqual(padStep(at, down, REPEAT_DELAY_MS + REPEAT_EVERY_MS - 1), { down: [], up: [] })
  assert.deepEqual(padStep(at, down, REPEAT_DELAY_MS + REPEAT_EVERY_MS), { down: ['ArrowDown'], up: [] })
  // Released (or the controller went away): both come up once, and a new press is fresh.
  assert.deepEqual(padStep(at, new Set(), 2000), { down: [], up: ['ArrowDown', 'Enter'] })
  assert.deepEqual(padStep(at, new Set(), 2010), { down: [], up: [] })
  assert.deepEqual(padStep(at, new Set(['Enter']), 2020), { down: ['Enter'], up: [] })
})

test('bumpers, triggers and the right stick repeat like arrows; X, Y, Start and View never do', () => {
  const repeating = ['PageUp', 'PageDown', 'Shift+PageUp', 'Shift+PageDown', 'AudioVolumeUp', 'AudioVolumeDown']
  const single = ['MediaPlayPause', 'Info', 'ContextMenu', 'Escape']
  const at = new Map()
  const all = new Set([...repeating, ...single])
  assert.deepEqual(padStep(at, all, 0).down.sort(), [...all].sort())
  assert.deepEqual(padStep(at, all, REPEAT_DELAY_MS - 1).down, [])
  assert.deepEqual(padStep(at, all, REPEAT_DELAY_MS).down.sort(), [...repeating].sort())
  assert.deepEqual(padStep(at, all, REPEAT_DELAY_MS + REPEAT_EVERY_MS).down.sort(), [...repeating].sort())
  assert.deepEqual(padStep(at, new Set(), 5000).up.sort(), [...all].sort())
})
