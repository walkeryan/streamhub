import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  COUNTDOWN_MS, UP_NEXT_IDLE, asksStillWatching, countdownSeconds, creditsAt, episodeCode, episodeLine, introAt, lastCredits,
  streakAfterAutoplay, streakAfterInput, thumbnailAt, upNextReducer, upNextStart,
} from '../src/player/upNext.ts'

const MIN = 60_000
const markers = [
  { type: 'intro', start_ms: 30_000, end_ms: 95_000 },
  { type: 'credits', start_ms: 20 * MIN, end_ms: 21 * MIN },
  { type: 'credits', start_ms: 48 * MIN, end_ms: 50 * MIN },
]

test('skip intro shows inside the intro only, and stops just before its end', () => {
  assert.equal(introAt(markers, 29_999), null)
  assert.equal(introAt(markers, 30_000)?.end_ms, 95_000)
  assert.equal(introAt(markers, 94_000)?.end_ms, 95_000)
  assert.equal(introAt(markers, 94_600), null)
  assert.equal(introAt([], 40_000), null)
  assert.equal(introAt(null, 40_000), null)
})

test('the last credits marker is where Up next comes in; without one, the last 30 s', () => {
  assert.equal(lastCredits(markers)?.start_ms, 48 * MIN)
  assert.equal(creditsAt(markers, 20 * MIN + 5), null) // an earlier credits block is not the end
  assert.equal(creditsAt(markers, 49 * MIN)?.start_ms, 48 * MIN)
  assert.equal(upNextStart(markers, 50 * MIN), 48 * MIN)
  assert.equal(upNextStart([], 50 * MIN), 50 * MIN - 30_000)
  assert.equal(upNextStart([], null), null)
  // a clip shorter than a minute gets the card in its last third
  assert.equal(upNextStart([], 30_000), 20_000)
})

const at = (state, positionMs, startMs = 48 * MIN) => upNextReducer(state, { type: 'position', positionMs, startMs })
const tick = (state, ms, playing = true, autoplay = true) => upNextReducer(state, { type: 'tick', ms, playing, autoplay })

test('the card counts ten seconds while the credits play, then fires', () => {
  let s = at(UP_NEXT_IDLE, 47 * MIN)
  assert.equal(s.phase, 'hidden')
  s = at(s, 48 * MIN)
  assert.deepEqual([s.phase, s.remainingMs, s.fire], ['credits', COUNTDOWN_MS, false])
  s = tick(s, 4_000)
  assert.equal(countdownSeconds(s.remainingMs), 6)
  s = tick(s, 4_000, false) // paused: the count waits
  assert.equal(s.remainingMs, 6_000)
  s = tick(s, 6_000)
  assert.equal(s.fire, true)
  assert.equal(countdownSeconds(0), 1)
  assert.deepEqual(upNextReducer(s, { type: 'reset' }), UP_NEXT_IDLE)
})

test('autoplay off: the card shows and never counts', () => {
  let s = at(UP_NEXT_IDLE, 48 * MIN)
  s = tick(s, 60_000, true, false)
  assert.deepEqual([s.phase, s.remainingMs, s.fire], ['credits', COUNTDOWN_MS, false])
  s = upNextReducer(s, { type: 'ended', autoplay: false })
  assert.deepEqual([s.phase, s.fire], ['credits', false])
})

test('dismissed, the episode plays to its end and the card returns with a fresh count', () => {
  let s = tick(at(UP_NEXT_IDLE, 48 * MIN), 7_000)
  s = upNextReducer(s, { type: 'dismiss' })
  assert.deepEqual([s.phase, s.dismissed], ['hidden', true])
  s = at(s, 49 * MIN)
  assert.equal(s.phase, 'hidden')
  s = upNextReducer(s, { type: 'ended', autoplay: true })
  assert.deepEqual([s.phase, s.remainingMs, s.fire], ['end', COUNTDOWN_MS, false])
  s = tick(s, 9_000, false) // after the end nothing plays, and the count still runs
  s = tick(s, 1_000, false)
  assert.equal(s.fire, true)
})

test('any seek dismisses the card; going back before the credits brings it back there', () => {
  let s = at(UP_NEXT_IDLE, 48 * MIN)
  s = upNextReducer(s, { type: 'seek', toMs: 49 * MIN, startMs: 48 * MIN })
  assert.deepEqual([s.phase, s.dismissed], ['hidden', true])
  s = upNextReducer(s, { type: 'seek', toMs: 10 * MIN, startMs: 48 * MIN })
  assert.equal(s.dismissed, false)
  assert.equal(at(s, 48 * MIN).phase, 'credits')
  // seeking back from the end card: the picture plays again and the card waits for the end
  const end = upNextReducer(upNextReducer(UP_NEXT_IDLE, { type: 'dismiss' }), { type: 'ended', autoplay: true })
  assert.equal(end.phase, 'end')
  assert.deepEqual(upNextReducer(end, { type: 'seek', toMs: 30 * MIN, startMs: 48 * MIN }).phase, 'hidden')
})

test('the picture running out while the card counts plays the next one at once', () => {
  const s = upNextReducer(tick(at(UP_NEXT_IDLE, 48 * MIN), 2_000), { type: 'ended', autoplay: true })
  assert.equal(s.fire, true)
})

test('still watching: three autoplays in a row without input, and the fourth waits', () => {
  let streak = 0
  for (let i = 0; i < 3; i++) { assert.equal(asksStillWatching(streak), false); streak = streakAfterAutoplay(streak) }
  assert.equal(asksStillWatching(streak), true)
  streak = streakAfterInput()
  assert.equal(asksStillWatching(streak), false)
})

test('episode lines and preview addresses', () => {
  assert.equal(episodeLine('The Lighthouse Keepers', 2, 1), 'The Lighthouse Keepers · Season 2 · Episode 1')
  assert.equal(episodeLine(null, null, 4), 'Episode 4')
  assert.equal(episodeCode(2, 3), 'S2 · E3')
  assert.equal(episodeCode(null, 3), 'E3')
  assert.equal(thumbnailAt('/api/v1/thumbnails/t', 2000, 61_999), '/api/v1/thumbnails/t?ms=60000')
  assert.equal(thumbnailAt('/x?a=1', 0, 5_000), '/x?a=1&ms=4000')
})
