import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mirrorVideoFrames } from '../src/player/mirrorVideoFrames.ts'

function setup(t, frames = true) {
  const page = new EventTarget()
  const callbacks = new Map()
  let serial = 0
  const schedule = callback => { callbacks.set(++serial, callback); return serial }
  const cancel = id => callbacks.delete(id)
  const globals = { document: page, HTMLMediaElement: { HAVE_CURRENT_DATA: 2 }, requestAnimationFrame: schedule, cancelAnimationFrame: cancel }
  for (const [key, value] of Object.entries(globals)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { value, configurable: true })
    t.after(() => { if (original) Object.defineProperty(globalThis, key, original); else delete globalThis[key] })
  }
  const video = Object.assign(new EventTarget(), {
    videoWidth: 640, videoHeight: 360, currentTime: 1, readyState: 2, paused: false,
    textTracks: Object.assign(new EventTarget(), { length: 0 }),
    ...(frames && { requestVideoFrameCallback: schedule, cancelVideoFrameCallback: cancel }),
  })
  const draws = []
  const canvas = { hidden: true, getContext: () => ({ drawImage: (...args) => draws.push(args) }) }
  const stop = mirrorVideoFrames(video, canvas)
  return { canvas, video, stop, draws, callbacks, tick: () => {
    assert.equal(callbacks.size, 1)
    const [id, run] = callbacks.entries().next().value
    callbacks.delete(id)
    run()
  } }
}

test('decoded frames resize the canvas and switching off releases the pending callback and listeners', t => {
  const { canvas, video, stop, draws, callbacks, tick } = setup(t)
  assert.deepEqual(draws[0], [video, 0, 0, 640, 360])
  video.videoWidth = 1280
  video.videoHeight = 720
  tick()
  assert.deepEqual([canvas.width, canvas.height], [1280, 720])
  stop()
  video.dispatchEvent(new Event('playing'))
  assert.equal(canvas.hidden, true)
  assert.equal(callbacks.size, 0)
  assert.equal(draws.length, 2)
})

test('the fallback skips duplicate frames and redraws a paused seek without starting another loop', t => {
  const { canvas, video, stop, draws, callbacks, tick } = setup(t, false)
  tick()
  assert.equal(draws.length, 1)
  video.currentTime = 2
  tick()
  assert.equal(draws.length, 2)
  video.paused = true
  video.dispatchEvent(new Event('seeking'))
  assert.ok(canvas.hidden)
  video.dispatchEvent(new Event('seeked'))
  assert.equal(canvas.hidden, false)
  assert.equal(callbacks.size, 0)
  assert.equal(draws.length, 3)
  stop()
})

test('captions and protected media show the original video, and a new unprotected source can resume drawing', t => {
  const { canvas, video, callbacks, stop } = setup(t)
  Object.assign(video.textTracks, { 0: { kind: 'captions', mode: 'showing' }, length: 1 })
  video.textTracks.dispatchEvent(new Event('change'))
  assert.ok(canvas.hidden)
  assert.equal(callbacks.size, 0)
  video.textTracks[0].mode = 'disabled'
  video.textTracks.dispatchEvent(new Event('change'))
  assert.equal(canvas.hidden, false)
  video.dispatchEvent(new Event('encrypted'))
  video.dispatchEvent(new Event('playing'))
  assert.ok(canvas.hidden)
  video.dispatchEvent(new Event('emptied'))
  video.dispatchEvent(new Event('loadeddata'))
  assert.equal(canvas.hidden, false)
  stop()
})
