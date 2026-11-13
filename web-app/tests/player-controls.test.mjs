import { test } from 'node:test'
import assert from 'node:assert/strict'
import { COMMIT_MS, HOLD_STEP_MS, accumulate, clampTarget, endTarget, fractionTarget, holdStep, jump, seekHint } from '../src/player/seekSteps.ts'
import { SHORTCUT_HELP, shortcutFor } from '../src/player/shortcuts.ts'
import { DEFAULT_PREFS, SPEEDS, clampSpeed, nextSpeed, nextVolume, parsePrefs, speedLabel } from '../src/player/playerPrefs.ts'

const FILM = 5400

test('separate presses add ten seconds each to one pending target', () => {
  let pending = null
  // Four taps, 300 ms apart, from 600 s.
  for (let i = 0; i < 4; i++) pending = accumulate(pending, { dir: 1, now: 1000 + i * 300 }, 600, FILM)
  assert.equal(pending.base, 600)
  assert.equal(pending.target, 640)
  assert.equal(seekHint(pending.target - pending.base), '+40 s')
  // However fast separate presses arrive (a script, a very quick thumb), each one counts.
  let burst = null
  for (let i = 0; i < 4; i++) burst = accumulate(burst, { dir: 1, now: 5000 + i * 8 }, 600, FILM)
  assert.equal(burst.target, 640)
  // A press the other way comes off the same target; the base stays where the presses began.
  pending = accumulate(pending, { dir: -1, now: 2200 }, 999, FILM)
  assert.deepEqual([pending.base, pending.target, pending.dir], [600, 630, -1])
  // Shift is a minute.
  assert.equal(accumulate(null, { dir: -1, now: 0, big: true }, 600, FILM).target, 540)
  assert.ok(COMMIT_MS >= 400 && COMMIT_MS <= 600)
})

test('a held key moves at most every 100 ms and its steps grow: 10 s, then 30 s, then 60 s', () => {
  assert.deepEqual([0, 999, 1000, 2999, 3000, 60_000].map(holdStep), [10, 10, 30, 30, 60, 60])
  let pending = accumulate(null, { dir: 1, now: 0 }, 0, FILM)
  let moves = 1
  // The keyboard repeats every 30 ms for four seconds.
  for (let now = 30; now <= 4000; now += 30) {
    const next = accumulate(pending, { dir: 1, now, held: true }, 0, FILM)
    if (next.target !== pending.target) moves++
    pending = next
  }
  // About ten moves a second, not one per repeat.
  assert.ok(moves >= 30 && moves <= 41, `moves ${moves}`)
  assert.ok(HOLD_STEP_MS === 100)
  // One second of tens, two of thirties, one of sixties: far further than forty tens.
  assert.ok(pending.target > 900 && pending.target < 1500, `target ${pending.target}`)
  // A remote that repeats without saying so (presses 80 ms apart) is treated the same way.
  let quiet = accumulate(null, { dir: -1, now: 0 }, 3000, FILM)
  for (let now = 80; now <= 2000; now += 80) quiet = accumulate(quiet, { dir: -1, now }, 3000, FILM)
  // Believed to be held after 400 ms, then one move per 160 ms: fewer moves than presses, in growing steps.
  assert.ok(3000 - quiet.target >= 200, `moved ${3000 - quiet.target}`)
  // Letting go and pressing again starts over at ten seconds.
  const again = accumulate(pending, { dir: 1, now: 9000 }, 0, FILM)
  assert.equal(again.target - pending.target, 10)
  assert.equal(again.heldSince, null)
})

test('a target never leaves the title, and never lands on its last second', () => {
  assert.equal(accumulate(null, { dir: -1, now: 0 }, 4, FILM).target, 0)
  assert.equal(accumulate(null, { dir: 1, now: 0, big: true }, FILM - 5, FILM).target, FILM - 1)
  assert.equal(clampTarget(99, 0), 99) // a length nobody knows limits nothing
  assert.equal(clampTarget(-3, 0), 0)
})

test('number keys, Home and End are jumps that wait like steps do', () => {
  assert.equal(fractionTarget(0, FILM), 0)
  assert.equal(fractionTarget(5, FILM), 2700)
  assert.equal(fractionTarget(9, FILM), 4860)
  assert.equal(fractionTarget(5, 0), 0)
  assert.equal(endTarget(FILM), FILM - 10)
  assert.equal(endTarget(4), 0)
  const first = jump(null, fractionTarget(3, FILM), 600, FILM, 0)
  assert.deepEqual([first.base, first.target, first.dir], [600, 1620, 1])
  // A second number replaces the target and keeps the base: still one seek when it is committed.
  const second = jump(first, fractionTarget(0, FILM), 9999, FILM, 200)
  assert.deepEqual([second.base, second.target, second.dir], [600, 0, -1])
  // A step after a jump carries on from the jump's target.
  assert.equal(accumulate(second, { dir: 1, now: 900 }, 0, FILM).target, 10)
})

test('the hint reads like a distance', () => {
  assert.equal(seekHint(10), '+10 s')
  assert.equal(seekHint(-50), '−50 s')
  assert.equal(seekHint(60), '+1 min')
  assert.equal(seekHint(-90), '−1 min 30 s')
  assert.equal(seekHint(3600), '+1 h')
  assert.equal(seekHint(3900), '+1 h 5 min')
  assert.equal(seekHint(0), '+0 s')
})

const film = { live: false, room: false, navigating: false }
const key = (k, extra = {}) => ({ key: k, ...extra })

test('the shortcut map for a film', () => {
  for (const k of [' ', 'k', 'K', 'MediaPlayPause']) assert.deepEqual(shortcutFor(key(k), film), { type: 'toggle-play' })
  assert.deepEqual(shortcutFor(key('j'), film), { type: 'seek-step', dir: -1, big: false })
  assert.deepEqual(shortcutFor(key('L'), film), { type: 'seek-step', dir: 1, big: false })
  assert.deepEqual(shortcutFor(key('ArrowLeft'), film), { type: 'seek-step', dir: -1, big: false })
  assert.deepEqual(shortcutFor(key('ArrowRight', { shiftKey: true }), film), { type: 'seek-step', dir: 1, big: true })
  assert.deepEqual(shortcutFor(key('MediaRewind'), film), { type: 'seek-step', dir: -1, big: false })
  assert.deepEqual(shortcutFor(key('MediaFastForward'), film), { type: 'seek-step', dir: 1, big: false })
  assert.deepEqual(shortcutFor(key('7'), film), { type: 'seek-fraction', digit: 7 })
  assert.deepEqual(shortcutFor(key('Home'), film), { type: 'seek-start' })
  assert.deepEqual(shortcutFor(key('End'), film), { type: 'seek-end' })
  assert.deepEqual(shortcutFor(key('<', { shiftKey: true }), film), { type: 'speed', dir: -1 })
  assert.deepEqual(shortcutFor(key('>', { shiftKey: true }), film), { type: 'speed', dir: 1 })
  assert.deepEqual(shortcutFor(key('m'), film), { type: 'mute' })
  assert.deepEqual(shortcutFor(key('ArrowUp'), film), { type: 'volume', dir: 1 })
  assert.deepEqual(shortcutFor(key('ArrowDown'), film), { type: 'volume', dir: -1 })
  assert.deepEqual(shortcutFor(key('c'), film), { type: 'subtitles' })
  assert.deepEqual(shortcutFor(key('a'), film), { type: 'menu', section: 'audio' })
  assert.deepEqual(shortcutFor(key('s'), film), { type: 'menu', section: 'settings' })
  assert.deepEqual(shortcutFor(key('O'), film), { type: 'menu', section: 'settings' })
  assert.deepEqual(shortcutFor(key('ContextMenu'), film), { type: 'menu', section: 'all' })
  assert.deepEqual(shortcutFor(key('MediaTrackNext'), film), { type: 'menu', section: 'all' })
  assert.deepEqual(shortcutFor(key('f'), film), { type: 'fullscreen' })
  assert.deepEqual(shortcutFor(key('p'), film), { type: 'pip' })
  assert.deepEqual(shortcutFor(key('i'), film), { type: 'info' })
  assert.deepEqual(shortcutFor(key('?', { shiftKey: true }), film), { type: 'help' })
  assert.deepEqual(shortcutFor(key('MediaStop'), film), { type: 'stop' })
  // Enter, Escape and Backspace are not shortcuts: they commit, cancel and go back.
  for (const k of ['Enter', 'Escape', 'Backspace', 'Tab', 'x']) assert.equal(shortcutFor(key(k), film), null)
})

test('a modifier other than Shift, or a text field, leaves the key alone', () => {
  for (const extra of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
    assert.equal(shortcutFor(key('f', extra), film), null)
    assert.equal(shortcutFor(key('ArrowLeft', extra), film), null)
  }
  assert.equal(shortcutFor(key('k'), { ...film, typing: true }), null)
  assert.equal(shortcutFor(key(' '), { ...film, typing: true }), null)
})

test('inside the control bar the arrows navigate; on the scrubber Left and Right still seek', () => {
  const bar = { ...film, navigating: true }
  for (const k of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) assert.equal(shortcutFor(key(k), bar), null)
  assert.deepEqual(shortcutFor(key('ArrowRight'), { ...bar, scrubbing: true }), { type: 'seek-step', dir: 1, big: false })
  assert.equal(shortcutFor(key('ArrowUp'), { ...bar, scrubbing: true }), null)
  // Letters are not navigation: they work wherever focus is.
  assert.deepEqual(shortcutFor(key('f'), bar), { type: 'fullscreen' })
  assert.deepEqual(shortcutFor(key('j'), bar), { type: 'seek-step', dir: -1, big: false })
  assert.deepEqual(shortcutFor(key('4'), bar), { type: 'seek-fraction', digit: 4 })
})

test('live keeps zap on Up and Down and its digits for channel numbers; nothing seeks or changes speed', () => {
  const live = { live: true, room: false, navigating: false }
  for (const k of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'j', 'l', '5', 'Home', 'End', '<', '>', 'MediaRewind', 'MediaFastForward']) assert.equal(shortcutFor(key(k), live), null, k)
  assert.deepEqual(shortcutFor(key('k'), live), { type: 'toggle-play' })
  assert.deepEqual(shortcutFor(key('m'), live), { type: 'mute' })
  assert.deepEqual(shortcutFor(key('s'), live), { type: 'menu', section: 'settings' })
  // A room plays at one speed for everyone, but its film can be moved and its volume is each member's own.
  const room = { live: false, room: true, navigating: false }
  assert.equal(shortcutFor(key('>'), room), null)
  assert.deepEqual(shortcutFor(key('ArrowRight'), room), { type: 'seek-step', dir: 1, big: false })
  assert.deepEqual(shortcutFor(key('ArrowUp'), room), { type: 'volume', dir: 1 })
})

test('every shortcut the sheet lists is one the map knows', () => {
  const names = { Space: ' ', '←': 'ArrowLeft', '→': 'ArrowRight', '↑': 'ArrowUp', '↓': 'ArrowDown', 'Shift ←': 'ArrowLeft', 'Shift →': 'ArrowRight', Esc: null, Enter: null, '…': null }
  for (const group of SHORTCUT_HELP) for (const line of group.lines) for (const shown of line.keys) {
    const name = shown in names ? names[shown] : shown
    if (name === null) continue
    const context = { live: line.only === 'live', room: false, navigating: false }
    // Live's arrows and digits are the player's own zap keys, not entries of the map.
    if (line.only === 'live') { assert.equal(shortcutFor(key(name), context), null, shown); continue }
    assert.ok(shortcutFor(key(name, { shiftKey: shown.startsWith('Shift') }), context), `${shown} (${line.does})`)
  }
})

test('speed runs from 0.5 to 2 in quarters and volume in twentieths', () => {
  assert.deepEqual(SPEEDS, [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2])
  assert.equal(nextSpeed(1, 1), 1.25)
  assert.equal(nextSpeed(2, 1), 2)
  assert.equal(nextSpeed(0.5, -1), 0.5)
  assert.equal(clampSpeed(1.1), 1)
  assert.equal(clampSpeed(9), 2)
  assert.equal(clampSpeed('fast'), 1)
  assert.equal(speedLabel(1), 'Normal')
  assert.equal(speedLabel(1.25), '1.25×')
  let volume = 1
  for (let i = 0; i < 10; i++) volume = nextVolume(volume, -1)
  assert.equal(volume, 0.5)
  assert.equal(nextVolume(0.02, -1), 0)
  assert.equal(nextVolume(0.98, 1), 1)
})

test('stored preferences survive anything a browser might hand back', () => {
  assert.deepEqual(parsePrefs(null), DEFAULT_PREFS)
  assert.deepEqual(parsePrefs('not json'), DEFAULT_PREFS)
  assert.deepEqual(parsePrefs('[1,2]'), DEFAULT_PREFS)
  assert.deepEqual(parsePrefs(JSON.stringify({ volume: 7, muted: 'yes', speed: 1.3, remaining: true, subtitle: 'English' })), { volume: 1, muted: false, speed: 1.25, remaining: true, subtitle: 'English' })
  assert.deepEqual(parsePrefs(JSON.stringify({ volume: 0.35, muted: true, speed: 0.1 })), { volume: 0.35, muted: true, speed: 0.5, remaining: false, subtitle: null })
})
