import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bridgeOf, expiresSoon, roomPlay, shellViewport, titlePlay, SHELL_WIDTH } from '../src/shellProtocol.ts'

const bridge = { play: () => true, signedOut: () => {}, capabilities: () => '{}', deliverToken: () => {} }
const noConsent = () => false

test('an ordinary browser has no bridge, so shell mode never starts', () => {
  assert.equal(bridgeOf({}), null)
  assert.equal(bridgeOf(undefined), null)
  assert.equal(bridgeOf(null), null)
  assert.equal(bridgeOf({ StreamHubTV: null }), null)
  assert.equal(bridgeOf({ StreamHubTV: 'yes' }), null)
  // Something else that happens to be called StreamHubTV is not the bridge: every function must be there.
  assert.equal(bridgeOf({ StreamHubTV: { play: () => true } }), null)
  assert.equal(bridgeOf({ StreamHubTV: { ...bridge, deliverToken: 1 } }), null)
})

test('the Android TV app is recognised by its bridge', () => {
  assert.equal(bridgeOf({ StreamHubTV: bridge }), bridge)
})

test('Play hands the TV what the web player would have resolved', () => {
  assert.deepEqual(titlePlay('/play/tmdb%3Amovie%3A603', noConsent), { v: 1, kind: 'title', id: 'tmdb:movie:603' })
  assert.deepEqual(titlePlay('/play/tmdb%3Amovie%3A603?variant=v2&source=plex-1&audio=1', noConsent),
    { v: 1, kind: 'title', id: 'tmdb:movie:603', variant_id: 'v2', source_id: 'plex-1', audio_track: 1 })
  assert.deepEqual(titlePlay('/play/ch%3A1?catchup=prog%3A9', noConsent), { v: 1, kind: 'title', id: 'ch:1', catchup_programme_id: 'prog:9' })
  assert.deepEqual(titlePlay('/play/ch%3A1?rewind=2026-11-13T20%3A00%3A00Z', noConsent), { v: 1, kind: 'title', id: 'ch:1', rewind_at: '2026-11-13T20:00:00Z' })
  assert.deepEqual(titlePlay('/play/rec%3A42', noConsent), { v: 1, kind: 'title', id: 'rec:42' })
  // A channel list from the Channels screen is the TV's own business (the descriptor carries it).
  assert.deepEqual(titlePlay('/play/ch%3A1?list=l1', noConsent), { v: 1, kind: 'title', id: 'ch:1' })
})

test('audio consent travels only when this page granted it, for this choice', () => {
  const seen = []
  const consent = (token, id, variantId, scope) => { seen.push({ token, id, variantId, scope }); return token === 'granted' }
  assert.equal(titlePlay('/play/x%3A1?variant=v2&audio_choice=granted&source=s1', consent).allow_unverified_audio, true)
  assert.deepEqual(seen[0], { token: 'granted', id: 'x:1', variantId: 'v2', scope: { sourceId: 's1', catchupProgrammeId: null, rewindAt: null } })
  assert.equal(titlePlay('/play/x%3A1?variant=v2&audio_choice=forged', consent).allow_unverified_audio, undefined)
  // Without a variant there is nothing the consent or a track applies to.
  assert.deepEqual(titlePlay('/play/x%3A1?audio_choice=granted&audio=2', consent), { v: 1, kind: 'title', id: 'x:1' })
  assert.equal(titlePlay('/play/x%3A1?variant=v2&audio=999', noConsent).audio_track, undefined)
})

test('only player addresses are handed over', () => {
  assert.equal(titlePlay('/title/tmdb%3Amovie%3A603', noConsent), null)
  assert.equal(titlePlay('/play/', noConsent), null)
  assert.equal(titlePlay('/play/a/b', noConsent), null)
  assert.equal(titlePlay('/play/%E0%A4%A', noConsent), null)
})

test('a room is handed over as its code and the seat the page took', () => {
  assert.deepEqual(roomPlay('abc234', 'mt-1', true), { v: 1, kind: 'room', room_code: 'ABC234', member_token: 'mt-1', allow_unverified_audio: true })
})

test('the shell lays out 1920 CSS pixels whatever width the WebView reports', () => {
  assert.equal(SHELL_WIDTH, 1920)
  assert.equal(shellViewport(960), 'width=1920, initial-scale=0.5, minimum-scale=0.5, maximum-scale=0.5, user-scalable=no, viewport-fit=cover')
  assert.match(shellViewport(1280), /initial-scale=0\.667/)
  assert.match(shellViewport(1920), /initial-scale=1,/)
  assert.match(shellViewport(3840), /initial-scale=1,/)
  assert.match(shellViewport(0), /width=1920, initial-scale=0\.5/)
})

test('an access token is refreshed for the TV shortly before it expires', () => {
  const jwt = (exp) => `h.${Buffer.from(JSON.stringify({ exp })).toString('base64url')}.s`
  const now = 1_800_000_000_000
  assert.equal(expiresSoon(jwt(now / 1000 + 600), now), false)
  assert.equal(expiresSoon(jwt(now / 1000 + 10), now), true)
  assert.equal(expiresSoon(jwt(now / 1000 - 10), now), true)
  assert.equal(expiresSoon('opaque-token', now), false)
  assert.equal(expiresSoon('h.not-base64!.s', now), false)
})
