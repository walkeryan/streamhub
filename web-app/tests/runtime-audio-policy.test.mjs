import { test } from 'node:test'
import assert from 'node:assert/strict'
import { audioAllowed, audioLanguage, audioSelection, preferredAudioTrack } from '../src/player/audioPolicy.ts'
const fr = { id: 0, label: 'French default', language: 'fra', active: true }
const en = { id: 1, label: 'English', language: 'eng', active: false }
test('foreign default requires confirmed English selection, not a requested switch', () => {
  assert.equal(preferredAudioTrack(['en-US'], [fr, en]), 1)
  assert.equal(audioAllowed(['eng'], [fr, en], null), false)
  assert.equal(audioAllowed(['en'], [{ ...fr, active: false }, { ...en, active: true }], null), true)
})
test('unknown metadata and provider-like labels never establish English', () => {
  for (const language of [undefined, '', 'und', 'unknown', 'mul', 'zxx']) {
    const tracks = [{ id: 0, label: 'EN | English', language, active: true }]
    assert.equal(audioLanguage(language), null)
    assert.equal(audioAllowed(['en'], tracks, null), false)
    assert.equal(preferredAudioTrack(['en'], tracks), null)
  }
  assert.equal(audioAllowed(['en'], [], null), false)
})
test('explicit consent binds selected identity and metadata, not all foreign tracks', () => {
  const consent = audioSelection([fr, en])
  assert.equal(audioAllowed(['en'], [fr, en], consent), true)
  assert.equal(audioAllowed(['en'], [{ ...fr, language: 'deu' }], consent), false)
  assert.equal(audioAllowed(['en'], [{ ...fr, id: 2 }], consent), false)
  assert.equal(audioAllowed(['en'], [{ ...fr, active: false }], consent), false)
  assert.equal(audioAllowed(['en'], [fr], null), false)
})
test('ambiguous simultaneous active tracks cannot be consented implicitly', () => {
  assert.equal(audioSelection([fr, { ...en, active: true }]), null)
  assert.equal(audioAllowed(['en'], [fr, { ...en, active: true }], null), false)
})
