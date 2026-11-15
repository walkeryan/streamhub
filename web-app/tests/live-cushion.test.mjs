import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LIVE_CUSHION_S, LIVE_HOLD_MAX_MS, bufferedAhead, liveCushion } from '../src/player/liveCushion.ts'

function fakeVideo({ currentTime = 10, ranges = [[0, 10.5]], playbackRate = 1, paused = false } = {}) {
  const listeners = {}
  const v = {
    currentTime, paused, ranges,
    _rate: playbackRate,
    get playbackRate() { return this._rate },
    set playbackRate(x) { this._rate = x; (listeners.ratechange ?? []).forEach((f) => f()) },
    get buffered() { const r = this.ranges; return { length: r.length, start: (i) => r[i][0], end: (i) => r[i][1] } },
    addEventListener: (t, f) => { (listeners[t] ??= []).push(f) },
    removeEventListener: (t, f) => { listeners[t] = (listeners[t] ?? []).filter((g) => g !== f) },
    fire: (t) => (listeners[t] ?? []).forEach((f) => f()),
    listeners,
  }
  return v
}

function fakeClock() {
  let now = 0; let tick = null
  return { now: () => now, every: (fn) => { tick = fn; return () => { tick = null } }, advance(ms) { now += ms; tick?.() }, running: () => tick !== null }
}

test('bufferedAhead measures the range under the playhead and steps over a small leading gap', () => {
  assert.equal(bufferedAhead({ currentTime: 10, buffered: fakeVideo({ ranges: [[0, 13]] }).buffered }), 3)
  assert.equal(bufferedAhead({ currentTime: 0, buffered: fakeVideo({ ranges: [[0.2, 5.2]] }).buffered }), 5)
  assert.equal(bufferedAhead({ currentTime: 10, buffered: fakeVideo({ ranges: [[0, 9], [12, 20]] }).buffered }), 0)
  assert.equal(bufferedAhead({ currentTime: 10, buffered: fakeVideo({ ranges: [] }).buffered }), 0)
})

test('a stall holds the picture until the reserve is back, then plays on at the same speed', () => {
  const v = fakeVideo(); const clock = fakeClock(); const events = []
  const c = liveCushion(v, (h) => events.push(h), clock)
  v.fire('waiting')
  assert.equal(c.holding(), true)
  assert.equal(v.playbackRate, 0)
  v.ranges = [[0, 12]]; clock.advance(250)
  assert.equal(c.holding(), true, 'two seconds in hand is not yet a reserve')
  v.ranges = [[0, 10 + LIVE_CUSHION_S]]; clock.advance(250)
  assert.equal(c.holding(), false)
  assert.equal(v.playbackRate, 1)
  assert.deepEqual(events, [true, false])
})

test('a feed slower than real time plays what it has once the longest hold is up', () => {
  const v = fakeVideo(); const clock = fakeClock()
  const c = liveCushion(v, () => {}, clock)
  v.fire('waiting')
  clock.advance(LIVE_HOLD_MAX_MS - 250)
  assert.equal(c.holding(), true)
  clock.advance(250)
  assert.equal(c.holding(), false)
  assert.equal(v.playbackRate, 1)
})

test('an empty gap does not use up the limit: it counts from when video returns', () => {
  const v = fakeVideo({ ranges: [[0, 10]] }); const clock = fakeClock()
  const c = liveCushion(v, () => {}, clock)
  v.fire('waiting')
  clock.advance(LIVE_HOLD_MAX_MS + 5000)
  assert.equal(c.holding(), true, 'still nothing to play')
  v.ranges = [[0, 11]]; clock.advance(250)
  clock.advance(LIVE_HOLD_MAX_MS - 500)
  assert.equal(c.holding(), true)
  clock.advance(500)
  assert.equal(c.holding(), false)
})

test('no hold while paused by the viewer or while the reserve is already there', () => {
  const paused = fakeVideo({ paused: true })
  const a = liveCushion(paused, () => {}, fakeClock())
  paused.fire('waiting')
  assert.equal(a.holding(), false)
  const stocked = fakeVideo({ ranges: [[0, 20]] })
  const b = liveCushion(stocked, () => {}, fakeClock())
  stocked.fire('waiting')
  assert.equal(b.holding(), false)
  assert.equal(stocked.playbackRate, 1)
})

test('a speed the viewer picks during a hold ends it and is kept', () => {
  const v = fakeVideo({ playbackRate: 1.25 }); const clock = fakeClock(); const events = []
  const c = liveCushion(v, (h) => events.push(h), clock)
  v.fire('waiting')
  v.playbackRate = 1.5
  assert.equal(c.holding(), false)
  v.ranges = [[0, 20]]; clock.advance(250)
  assert.equal(v.playbackRate, 1.5)
  assert.deepEqual(events, [true, false])
})

test('stop restores the speed and removes every listener', () => {
  const v = fakeVideo({ playbackRate: 1.25 }); const clock = fakeClock()
  const c = liveCushion(v, () => {}, clock)
  v.fire('waiting')
  c.stop()
  assert.equal(v.playbackRate, 1.25)
  assert.equal(clock.running(), false)
  assert.equal((v.listeners.waiting ?? []).length + (v.listeners.ratechange ?? []).length, 0)
})
