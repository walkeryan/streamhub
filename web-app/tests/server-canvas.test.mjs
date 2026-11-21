import assert from 'node:assert/strict'
import { test } from 'node:test'
import { paintServerFrames, startCanvasTimeline } from '../src/player/serverCanvas.ts'

function pictureTag(deltaMs, payload = [255, 216]) {
  const owner = new TextEncoder().encode('streamhub.canvas.v1\0')
  const frameSize = owner.length + 4 + payload.length
  const tag = new Uint8Array(20 + frameSize)
  tag.set([73, 68, 51, 4, 0, 0, 0, 0, 0, 10 + frameSize])
  tag.set([80, 82, 73, 86, 0, 0, 0, frameSize], 10)
  tag.set(owner, 20)
  new DataView(tag.buffer).setInt32(20 + owner.length, deltaMs)
  tag.set(payload, tag.length - payload.length)
  return tag
}

test('the audio clock selects JPEG tiles, including the first segment and a backwards seek', (t) => {
  class ImageSheet {
    naturalWidth = 1280
    naturalHeight = 1080
    set src(_url) { this.onload() }
  }
  const globals = { document: new EventTarget(), Image: ImageSheet, cancelAnimationFrame() {} }
  for (const [name, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { configurable: true, value })
    t.after(() => { if (previous) Object.defineProperty(globalThis, name, previous); else delete globalThis[name] })
  }
  const audio = Object.assign(new EventTarget(), { paused: true, currentTime: 0.6, textTracks: new EventTarget() })
  const positions = []
  const canvas = { hidden: true, getContext: () => ({ drawImage: (_img, x, y) => positions.push([x, y]) }) }
  const timeline = startCanvasTimeline(audio)
  timeline.add(0, pictureTag(100))
  timeline.add(2, pictureTag(0))
  const stop = paintServerFrames(audio, canvas)
  assert.deepEqual(positions.at(-1), [640, 180]) // frame 6 of the first sheet
  assert.equal(canvas.width, 320)
  audio.currentTime = 2.01
  audio.dispatchEvent(new Event('seeked'))
  assert.deepEqual(positions.at(-1), [0, 0])
  audio.currentTime = 0.6
  audio.dispatchEvent(new Event('seeked'))
  assert.deepEqual(positions.at(-1), [640, 180])
  timeline.flush(0, 2)
  audio.dispatchEvent(new Event('timeupdate'))
  assert.equal(canvas.hidden, true)
  // Four independently decoded 2×3 JPEGs cover one HD segment; seek into its second sheet.
  timeline.add(4, pictureTag(0, [4, ...Array(4).fill([0, 0, 0, 2, 255, 216]).flat()]))
  audio.currentTime = 4.6
  audio.dispatchEvent(new Event('seeked'))
  assert.equal(canvas.width, 640)
  assert.equal(canvas.height, 360)
  assert.deepEqual(positions.at(-1), [640, 0])
  stop()
  timeline.stop()
})
