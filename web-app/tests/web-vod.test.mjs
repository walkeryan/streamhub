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

test('a source_busy answer becomes a heading and one line per stream', async () => {
  const { busyHeading, busyLines, parseSourceBusy } = await import('../src/player/sourceBusy.ts')
  const busy = parseSourceBusy({
    code: 'source_busy', message: 'x', limit: 2,
    active: [{ profile_name: 'Kids', title: 'Evening News', kind: 'channel', since: '2026-11-01T18:00:00+00:00' }, { profile_name: null, title: null }],
  })
  assert.equal(busyHeading(busy), 'Both streams are in use')
  assert.deepEqual(busyLines(busy), ['Kids is watching Evening News', 'Someone is watching something else'])
  assert.equal(busyHeading({ active: [], limit: 1 }), 'The stream is in use')
  assert.equal(busyHeading({ active: [], limit: 4 }), 'All 4 streams are in use')
  assert.equal(busyHeading({ active: [], limit: null }), 'No free stream')
  assert.equal(busyHeading({ active: [{}, {}], limit: null }), 'Both streams are in use')
  assert.deepEqual(parseSourceBusy({ code: 'source_busy', message: 'x' }), { active: [], limit: null })
  assert.equal(parseSourceBusy({ code: 'upstream_error', message: 'x' }), null)
  assert.equal(parseSourceBusy(null), null)
})

test('a stream URL that will not play says which kind of no it is', async () => {
  const { streamRefusal, AWAY_FROM_HOME } = await import('../src/player/sourceBusy.ts')
  // 409: the source, or the viewer's own limit, with who is watching what.
  assert.deepEqual(streamRefusal({ code: 'user_stream_limit', message: 'limit', limit: 1, active: [{ title: 'Harbor Lights' }] }), { busy: { active: [{ title: 'Harbor Lights' }], limit: 1, own: true } })
  // 403: not for this user here; asking again changes nothing.
  assert.deepEqual(streamRefusal({ code: 'remote_access_denied', message: 'remote access is not allowed for this user' }), { denied: AWAY_FROM_HOME })
  assert.deepEqual(streamRefusal({ code: 'content_restricted', message: 'Not available for this profile.' }), { denied: 'Not available for this profile.' })
  // 401: the session the URL was issued to; the API decides what that means for this device.
  for (const code of ['session_revoked', 'user_suspended', 'session_stale']) assert.deepEqual(streamRefusal({ code, message: '' }), { session: true })
  assert.equal(streamRefusal({ code: 'not_found', message: '' }), null)
  assert.equal(streamRefusal(undefined), null)
})
