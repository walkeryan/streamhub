import { test } from 'node:test'
import assert from 'node:assert/strict'
import { initialTrack, trackLabel } from '../src/player/audioTracks.ts'
import { seekPlan } from '../src/player/restartSeek.ts'

const french = { index: 0, language: 'fra', codec: 'ac3', channels: 6, title: null, default: false }
const english = { index: 1, language: 'eng', codec: 'eac3', channels: 2, title: null, default: true }

test('track labels name language, layout and codec', () => {
  assert.equal(trackLabel(french), 'French · 5.1 · AC-3')
  assert.equal(trackLabel(english), 'English · Stereo · E-AC-3')
  assert.equal(trackLabel({ index: 2, language: 'und', codec: 'pcm_s16le', channels: 4 }), 'Unknown language · 4 ch · PCM_S16LE')
  assert.equal(trackLabel({ index: 3, language: 'ces' }), 'CES')
})

test('the initial track is the preferred language, else the file default, else the first', () => {
  assert.equal(initialTrack([french, english], 0), 0)
  assert.equal(initialTrack([french, english], null), 1)
  assert.equal(initialTrack([french, { ...english, default: false }]), 0)
  assert.equal(initialTrack([french, english], 9), 1)
  assert.equal(initialTrack([]), null)
})

test('a seek inside what the stream produced stays in it; anything else restarts at the target', () => {
  // The stream began 600 s into a 5400 s film and has produced 90 s.
  const produced = [[0, 90]]
  assert.deepEqual(seekPlan(630, 600, produced, 5400), { within: 30 })
  assert.deepEqual(seekPlan(600, 600, produced, 5400), { within: 0 })
  assert.deepEqual(seekPlan(700, 600, produced, 5400), { restart: 700_000 })
  assert.deepEqual(seekPlan(590, 600, produced, 5400), { restart: 590_000 })
  assert.deepEqual(seekPlan(-20, 600, produced, 5400), { restart: 0 })
  assert.deepEqual(seekPlan(9999, 600, produced, 5400), { restart: 5_399_000 })
  assert.deepEqual(seekPlan(42, 0, [], 0), { restart: 42_000 })
})
