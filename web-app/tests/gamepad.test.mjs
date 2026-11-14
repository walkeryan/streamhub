import { test } from 'node:test'
import assert from 'node:assert/strict'
import { REPEAT_DELAY_MS, REPEAT_EVERY_MS, STICK_THRESHOLD, heldKeys, padStep } from '../src/nav/gamepadKeys.ts'

const pad = (buttons = [], axes = [0, 0, 0, 0]) => {
  const pressed = Array(17).fill(false)
  for (const b of buttons) pressed[b] = true
  return { pressed, axes }
}

test('D-pad, A and B are the remote keys; the left stick is the arrows past its threshold', () => {
  assert.deepEqual([...heldKeys([pad([12])])], ['ArrowUp'])
  assert.deepEqual([...heldKeys([pad([13, 0])])].sort(), ['ArrowDown', 'Enter'])
  assert.deepEqual([...heldKeys([pad([1])])], ['Escape'])
  assert.deepEqual([...heldKeys([pad([], [STICK_THRESHOLD, 0])])], ['ArrowRight'])
  assert.deepEqual([...heldKeys([pad([], [0, -0.9])])], ['ArrowUp'])
  // A stick resting a little off centre, the right stick and other buttons do nothing.
  assert.deepEqual([...heldKeys([pad([2, 3, 4, 9], [0.3, -0.2, 1, 1])])], [])
  // Two controllers: either one moves focus.
  assert.deepEqual([...heldKeys([pad([14]), pad([15])])].sort(), ['ArrowLeft', 'ArrowRight'])
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
