import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  STALL_SWITCH_MS, STARTUP_GRACE_MS, STEADY_RESET_MS,
  giveUp, nextFeed, observe, shouldSwitch, switchNotice, watchChannel, watchSwitched,
} from '../src/player/feedFailover.ts'

const feeds = (...ids) => ids.map((id) => ({ channel_id: id, channel_name: id.toUpperCase() }))

/** Feed one observation per second from `from` to `to` (exclusive). */
function run(watch, from, to, sample) {
  let w = watch
  for (let t = from; t < to; t += 1000) w = observe(w, { now: t, ...sample })
  return w
}

test('a channel that played and then stops for longer than the stall window switches', () => {
  let w = watchChannel('ch:a', 0)
  w = run(w, 0, 20_000, { progressing: true, paused: false })
  w = observe(w, { now: 20_000, progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 20_000 + STALL_SWITCH_MS), false, 'exactly the window is not more than it')
  assert.equal(shouldSwitch(w, 20_000 + STALL_SWITCH_MS + 1), true)
})

test('a frozen picture while video still arrives (a cushion hold) never switches, however long', () => {
  let w = run(watchChannel('ch:a', 0), 0, 10_000, { progressing: true, arriving: true, paused: false })
  // A gap, then a hold for the reserve: the picture stands still while video keeps coming in.
  w = run(w, 10_000, 13_000, { progressing: false, arriving: false, paused: false })
  w = run(w, 13_000, 40_000, { progressing: false, arriving: true, paused: false })
  assert.equal(w.troubleSince, null)
  assert.equal(shouldSwitch(w, 40_000), false)
  assert.equal(w.steadySince, null, 'a hold is not steady play')
})

test('no picture progress and no new video for longer than the window switches', () => {
  let w = run(watchChannel('ch:a', 0), 0, 10_000, { progressing: true, arriving: true, paused: false })
  w = run(w, 10_000, 12_000, { progressing: false, arriving: true, paused: false })
  w = observe(w, { now: 12_000, progressing: false, arriving: false, paused: false })
  assert.equal(shouldSwitch(w, 12_000 + STALL_SWITCH_MS), false)
  assert.equal(shouldSwitch(w, 12_000 + STALL_SWITCH_MS + 1), true, 'the clock starts when the video stops arriving')
})

test('video arriving does not count as having played, nor start a run\'s steady clock', () => {
  const origin = run(watchChannel('ch:a', 0), 0, 3_000, { progressing: true, paused: false })
  let w = watchSwitched(origin, 'ch:b', 10_000)
  w = run(w, 10_000, 50_000, { progressing: false, arriving: true, paused: false })
  assert.equal(w.played, false)
  assert.deepEqual(w.tried, ['ch:a', 'ch:b'])
  // Once the video stops arriving, a feed that never started has used up its start-up grace.
  w = observe(w, { now: 50_000, progressing: false, arriving: false, paused: false })
  assert.equal(shouldSwitch(w, 50_000), true)
})

test('start-up of a channel the viewer chose is never a stall', () => {
  let w = watchChannel('ch:a', 0)
  w = run(w, 0, 60_000, { progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 60_000), false)
})

test('a pause by the viewer is not a stall and clears the clock', () => {
  let w = run(watchChannel('ch:a', 0), 0, 5_000, { progressing: true, paused: false })
  w = observe(w, { now: 5_000, progressing: false, paused: false })
  w = run(w, 6_000, 60_000, { progressing: false, paused: true })
  assert.equal(w.troubleSince, null)
  assert.equal(shouldSwitch(w, 60_000), false)
})

test('a short gap that recovers resets the stall clock', () => {
  let w = run(watchChannel('ch:a', 0), 0, 5_000, { progressing: true, paused: false })
  w = run(w, 5_000, 9_000, { progressing: false, paused: false })
  w = observe(w, { now: 9_000, progressing: true, paused: false })
  w = observe(w, { now: 10_000, progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 14_000), false)
  assert.equal(shouldSwitch(w, 15_001), true)
})

test('the next feed skips the current one and every feed this run tried, in the server order', () => {
  let w = run(watchChannel('ch:b', 0), 0, 3_000, { progressing: true, paused: false })
  // The server lists the feeds after ch:b, wrapping round: c, d, a.
  assert.equal(nextFeed(w, feeds('ch:c', 'ch:d', 'ch:a')).channel_id, 'ch:c')
  w = watchSwitched(w, 'ch:c', 10_000)
  assert.deepEqual(w.tried, ['ch:b', 'ch:c'])
  assert.equal(nextFeed(w, feeds('ch:d', 'ch:a', 'ch:b')).channel_id, 'ch:d')
  w = watchSwitched(w, 'ch:d', 20_000)
  assert.equal(nextFeed(w, feeds('ch:a', 'ch:b', 'ch:c')).channel_id, 'ch:a')
  w = watchSwitched(w, 'ch:a', 30_000)
  assert.equal(nextFeed(w, feeds('ch:b', 'ch:c', 'ch:d')), null, 'each feed at most once per run')
  assert.equal(nextFeed(w, []), null)
})

test('a feed switched to has a start-up grace, after which not starting is a failure', () => {
  const origin = run(watchChannel('ch:a', 0), 0, 3_000, { progressing: true, paused: false })
  let w = watchSwitched(origin, 'ch:b', 10_000)
  w = run(w, 10_000, 10_000 + STARTUP_GRACE_MS + 1_000, { progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 10_000 + STARTUP_GRACE_MS), false)
  assert.equal(shouldSwitch(w, 10_000 + STARTUP_GRACE_MS + 1), true)
})

test('a run ends once a feed has played steadily, and every feed can be tried again', () => {
  const origin = run(watchChannel('ch:a', 0), 0, 3_000, { progressing: true, paused: false })
  let w = watchSwitched(origin, 'ch:b', 10_000)
  w = run(w, 10_000, 10_000 + STEADY_RESET_MS - 1_000, { progressing: true, paused: false })
  assert.deepEqual(w.tried, ['ch:a', 'ch:b'], 'not yet steady')
  w = run(w, 10_000 + STEADY_RESET_MS - 1_000, 10_000 + STEADY_RESET_MS + 2_000, { progressing: true, paused: false })
  assert.deepEqual(w.tried, [])
  assert.equal(nextFeed(w, feeds('ch:c', 'ch:a')).channel_id, 'ch:c')
})

test('giving up hands the stall to the player until a feed plays steadily again', () => {
  let w = run(watchChannel('ch:a', 0), 0, 3_000, { progressing: true, paused: false })
  w = run(w, 3_000, 10_000, { progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 10_000), true)
  w = giveUp(w)
  assert.equal(shouldSwitch(w, 60_000), false)
  w = run(w, 60_000, 60_000 + STEADY_RESET_MS + 1_000, { progressing: true, paused: false })
  assert.equal(w.exhausted, false)
  w = run(w, 100_000, 110_000, { progressing: false, paused: false })
  assert.equal(shouldSwitch(w, 110_000), true)
})

test('a channel the viewer chooses during a run starts a fresh watch', () => {
  const origin = run(watchChannel('ch:a', 0), 0, 3_000, { progressing: true, paused: false })
  const switched = watchSwitched(origin, 'ch:b', 10_000)
  assert.deepEqual(watchChannel('ch:z', 12_000).tried, [])
  assert.deepEqual(switched.tried, ['ch:a', 'ch:b'])
})

test('the notice names both channels', () => {
  assert.equal(switchNotice('Harbor Sports 1', 'Ridge Sports'), 'Harbor Sports 1 stopped — switched to Ridge Sports')
})
