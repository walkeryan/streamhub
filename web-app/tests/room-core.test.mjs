import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { RoomClock, clockSample } from '../src/room/roomClock.ts'
import { decide, positionAt, streamRestarted } from '../src/room/roomSync.ts'
import { RoomSocket, RECONNECT_DELAYS_MS } from '../src/room/RoomSocket.ts'

test('a pong gives the offset to the middle of its round trip', () => {
  // Sent at 1000, answered by a server whose clock reads 5060, received at 1100: the server is 4010 ahead.
  assert.deepEqual(clockSample(1000, 1100, 5060), { offsetMs: 4010, roundTripMs: 100 })
  assert.equal(clockSample(1100, 1000, 5000), null)
  assert.equal(clockSample(1000, 1100, Number.NaN), null)
})

test('the clock keeps the sample with the smallest round trip', () => {
  let now = 0
  const clock = new RoomClock(() => now)
  assert.equal(clock.synced, false)
  clock.seed(9000, 1000)
  assert.equal(clock.offsetMs, 8000)
  clock.sample(1000, 9300, 1400) // 400 ms round trip: offset 8100
  assert.equal(clock.synced, true)
  assert.equal(clock.offsetMs, 8100)
  clock.sample(2000, 10_020, 2040) // 40 ms round trip: offset 8000
  assert.equal(clock.offsetMs, 8000)
  clock.sample(3000, 11_500, 3900) // a slow one changes nothing
  assert.equal(clock.offsetMs, 8000)
  assert.equal(clock.roundTripMs, 40)
  now = 5000
  assert.equal(clock.serverNow(), 13_000)
})

test('an old best sample ages out of the window', () => {
  const clock = new RoomClock(() => 0)
  clock.sample(0, 500, 10) // the best there will be, from a clock that is later stepped
  for (let i = 1; i <= 24; i++) clock.sample(i * 100, i * 100 + 2050, i * 100 + 100)
  assert.equal(clock.offsetMs, 2000)
})

test('the room position moves with the server clock only while playing', () => {
  assert.equal(positionAt({ playing: true, position_ms: 60_000, as_of_ms: 1000 }, 3500), 62_500)
  assert.equal(positionAt({ playing: false, position_ms: 60_000, as_of_ms: 1000 }, 3500), 60_000)
  assert.equal(positionAt({ playing: true, position_ms: 60_000, as_of_ms: 1000 }, 900), 60_000)
  assert.equal(positionAt({ playing: true, position_ms: 60_000, as_of_ms: 1000 }, 99_000, 90_000), 90_000)
  assert.equal(positionAt({ playing: true, position_ms: null, as_of_ms: 1000 }, 3500), null)
})

test('a member within one second is left alone; further away seeks', () => {
  const playing = { playing: true, position_ms: 60_000, as_of_ms: 1000 }
  // Room is at 62 000 at server time 3000.
  assert.deepEqual(decide(playing, 3000, 62_000, true), { type: 'none' })
  assert.deepEqual(decide(playing, 3000, 61_000, true), { type: 'none' })
  assert.deepEqual(decide(playing, 3000, 63_000, true), { type: 'none' })
  assert.deepEqual(decide(playing, 3000, 60_999, true), { type: 'seek', positionMs: 62_000 })
  assert.deepEqual(decide(playing, 3000, 63_001, true), { type: 'seek', positionMs: 62_000 })
})

test('each call makes one correction: stop, then place, then start', () => {
  const paused = { playing: false, position_ms: 30_000, as_of_ms: 1000 }
  assert.deepEqual(decide(paused, 9000, 5000, true), { type: 'pause' })
  assert.deepEqual(decide(paused, 9000, 5000, false), { type: 'seek', positionMs: 30_000 })
  assert.deepEqual(decide(paused, 9000, 30_200, false), { type: 'none' })
  const playing = { playing: true, position_ms: 30_000, as_of_ms: 1000 }
  assert.deepEqual(decide(playing, 2000, 5000, false), { type: 'seek', positionMs: 31_000 })
  assert.deepEqual(decide(playing, 2000, 31_100, false), { type: 'play' })
  assert.deepEqual(decide(playing, 2000, 31_100, true), { type: 'none' })
})

test('a live room has no position: only play and pause', () => {
  const live = { playing: true, position_ms: null, as_of_ms: 1000 }
  assert.deepEqual(decide(live, 9000, 123_456, true), { type: 'none' })
  assert.deepEqual(decide(live, 9000, 0, false), { type: 'play' })
  assert.deepEqual(decide({ ...live, playing: false }, 9000, 0, true), { type: 'pause' })
})

test('a shared stream is loaded again only when its epoch is not the one in use', () => {
  assert.equal(streamRestarted(3, { stream_epoch: 4 }), true) // a seek restarted it
  assert.equal(streamRestarted(3, { stream_epoch: 3 }), false) // play, pause, or a seek to where it already starts
  assert.equal(streamRestarted(3, { stream_epoch: 1 }), true) // the room's stream was made anew
  // A title each member plays for themselves: no epoch on the state, nothing to reload, whatever the position did.
  assert.equal(streamRestarted(null, { stream_epoch: null }), false)
  assert.equal(streamRestarted(null, {}), false)
  assert.equal(streamRestarted(undefined, {}), false)
})

/* ---------- RoomSocket against a fake socket and fake timers ---------- */
class FakeSocket {
  static made = []
  sent = []
  closedWith = null
  constructor(url) { this.url = url; FakeSocket.made.push(this) }
  send(data) { this.sent.push(JSON.parse(data)) }
  close(code) { this.closedWith = code ?? 1005 }
  open() { this.onopen?.({}) }
  say(message) { this.onmessage?.({ data: JSON.stringify(message) }) }
  end(code) { this.onclose?.({ code }) }
  get types() { return this.sent.map((m) => m.type) }
}
function room(extra = {}) {
  FakeSocket.made = []
  const clock = new RoomClock(Date.now)
  const socket = new RoomSocket({ url: 'ws://hub.test/api/v1/rooms/ABC234/ws?member_token=t1', clock, createSocket: (url) => new FakeSocket(url), ...extra })
  return { clock, socket, last: () => FakeSocket.made.at(-1) }
}
const members = [{ id: 'm1', name: 'Ada', host: true, buffering: false }, { id: 'm2', name: 'Bo', host: false, buffering: true }]

test('the socket pings on open and every five seconds, and feeds pongs to the clock', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 1_000_000 })
  try {
    const { clock, socket, last } = room()
    socket.connect()
    assert.equal(socket.snapshot.status, 'connecting')
    assert.equal(socket.play(), false) // nothing is sent before the socket is open
    last().open()
    assert.equal(socket.snapshot.status, 'open')
    assert.deepEqual(last().sent, [{ type: 'ping', t: 1_000_000 }])
    mock.timers.tick(40)
    last().say({ type: 'pong', t: 1_000_000, server_ms: 1_000_520 })
    assert.equal(clock.offsetMs, 500)
    mock.timers.tick(5000)
    assert.deepEqual(last().sent.at(-1), { type: 'ping', t: 1_005_040 })
    socket.leave()
    assert.equal(last().closedWith, 1000)
    mock.timers.tick(60_000)
    assert.equal(FakeSocket.made.length, 1)
  } finally { mock.timers.reset() }
})

test('members, state, waiting and lock are surfaced; any state clears waiting', () => {
  const states = []
  const errors = []
  const { socket, last } = room({ onState: (state, previous) => states.push([state.position_ms, previous?.position_ms ?? null]), onError: (code) => errors.push(code) })
  let changes = 0
  const off = socket.subscribe(() => { changes++ })
  socket.connect(); last().open()
  last().say({ type: 'members', members })
  last().say({ type: 'state', playing: false, position_ms: 0, as_of_ms: 10, by: null, controls_locked: false })
  last().say({ type: 'waiting', name: 'Bo' })
  assert.deepEqual(socket.snapshot.members.map((m) => m.name), ['Ada', 'Bo'])
  assert.equal(socket.snapshot.waiting, 'Bo')
  last().say({ type: 'state', playing: true, position_ms: 4000, as_of_ms: 20, by: 'm1', controls_locked: true })
  assert.equal(socket.snapshot.waiting, null)
  assert.equal(socket.snapshot.controlsLocked, true)
  assert.equal(socket.snapshot.state.by, 'm1')
  assert.deepEqual(states, [[0, null], [4000, 0]])
  last().say({ type: 'state', playing: true, as_of_ms: 30 }) // live: no position
  assert.equal(socket.snapshot.state.position_ms, null)
  last().say({ type: 'error', code: 'controls_locked', message: 'Only the host can control playback.' })
  assert.deepEqual(errors, ['controls_locked'])
  last().onmessage({ data: 'not json' })
  assert.ok(changes >= 5)
  off()
  assert.equal(socket.play() && socket.pause() && socket.seek(61_234.6) && socket.lock(true), true)
  assert.deepEqual(last().sent.slice(-4), [{ type: 'play' }, { type: 'pause' }, { type: 'seek', position_ms: 61_235 }, { type: 'lock', on: true }])
  socket.leave()
})

test('a dropped connection is retried after 1, 2, 4, 8 and then 15 s with the same member token', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 0 })
  try {
    const { socket, last } = room()
    socket.connect(); last().open()
    last().say({ type: 'members', members })
    socket.setBuffering(true)
    for (const [i, wait] of [...RECONNECT_DELAYS_MS, 15_000, 15_000].entries()) {
      last().end(1006)
      assert.equal(socket.snapshot.status, 'reconnecting')
      mock.timers.tick(wait - 1)
      assert.equal(FakeSocket.made.length, i + 1, `no retry before ${wait} ms`)
      mock.timers.tick(1)
      assert.equal(FakeSocket.made.length, i + 2)
      assert.equal(last().url, 'ws://hub.test/api/v1/rooms/ABC234/ws?member_token=t1')
    }
    assert.deepEqual(RECONNECT_DELAYS_MS, [1000, 2000, 4000, 8000, 15_000])
    // Back: resynchronised by the server, and the member's own stall is reported again.
    last().open()
    assert.equal(socket.snapshot.status, 'open')
    assert.deepEqual(last().types, ['ping', 'buffering'])
    last().say({ type: 'state', playing: true, position_ms: 9000, as_of_ms: 5, by: null, controls_locked: false })
    assert.deepEqual(socket.snapshot.members.map((m) => m.id), ['m1', 'm2'])
    // An answer from the server starts the backoff over.
    last().end(1006)
    mock.timers.tick(1000)
    assert.equal(FakeSocket.made.length, RECONNECT_DELAYS_MS.length + 4)
    socket.leave()
  } finally { mock.timers.reset() }
})

test('closed{reason} and the final close codes end the room without a retry', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 0 })
  try {
    for (const [message, code, reason] of [[{ type: 'closed', reason: 'deleted' }, 4000, 'deleted'], [{ type: 'closed', reason: 'not_a_member' }, 4404, 'not_a_member'], [null, 4401, 'unauthorized'], [null, 4000, 'closed']]) {
      const { socket, last } = room()
      socket.connect(); last().open()
      last().say({ type: 'waiting', name: 'Bo' })
      if (message) last().say(message)
      last().end(code)
      assert.equal(socket.snapshot.status, 'closed')
      assert.equal(socket.snapshot.closed, reason)
      assert.equal(socket.snapshot.waiting, null)
      mock.timers.tick(60_000)
      assert.equal(FakeSocket.made.length, 1)
      assert.equal(socket.play(), false)
    }
  } finally { mock.timers.reset() }
})

test('a renewed member token is used by the next connection; the open one stays', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  try {
    const { socket, last } = room()
    socket.connect(); last().open()
    const first = last()
    socket.setUrl('ws://hub.test/api/v1/rooms/ABC234/ws?member_token=t2')
    assert.equal(first.closedWith, null)
    assert.equal(FakeSocket.made.length, 1)
    first.end(1006)
    mock.timers.tick(RECONNECT_DELAYS_MS[0])
    assert.equal(FakeSocket.made.length, 2)
    assert.equal(last().url.endsWith('member_token=t2'), true)
  } finally { mock.timers.reset() }
})

test('a shared-stream state carries its epoch and start; other rooms have neither', () => {
  const { socket, last } = room()
  socket.connect(); last().open()
  last().say({ type: 'state', playing: false, position_ms: 62_000, as_of_ms: 5000, by: 'm1', controls_locked: false, stream_epoch: 2, stream_start_ms: 60_000 })
  assert.equal(socket.snapshot.state.stream_epoch, 2)
  assert.equal(socket.snapshot.state.stream_start_ms, 60_000)
  last().say({ type: 'state', playing: true, position_ms: 62_000, as_of_ms: 6000, by: 'm1', controls_locked: false })
  assert.equal(socket.snapshot.state.stream_epoch, null)
  assert.equal(socket.snapshot.state.stream_start_ms, null)
  socket.leave()
})

test('a seat found gone elsewhere ends the room once, without a retry', () => {
  mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  try {
    const { socket, last } = room()
    socket.connect(); last().open()
    socket.end('not_a_member')
    assert.equal(socket.snapshot.closed, 'not_a_member')
    assert.equal(socket.snapshot.status, 'closed')
    socket.end('unauthorized') // the first reason stands
    assert.equal(socket.snapshot.closed, 'not_a_member')
    mock.timers.tick(60_000)
    assert.equal(FakeSocket.made.length, 1)
  } finally { mock.timers.reset() }
})
