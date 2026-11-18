import { test } from 'node:test'
import assert from 'node:assert/strict'
import { miniPlayerCommand, parseShellMessage } from '../src/shellProtocol.ts'

const state = {
  id: 'lib:plex:72', title: 'The Lighthouse Keepers', subtitle: 'S1 · E2 · The Storm', image: 'https://hub.example/api/v1/images/abc',
  position_ms: 61_000, duration_ms: 3_000_000, playing: true, has_next: true, has_previous: false, live: false,
}

test('the TV reports what plays in picture-in-picture, as text or already parsed', () => {
  const m = parseShellMessage(JSON.stringify({ v: 1, kind: 'mini_player', state }))
  assert.deepEqual(m, { kind: 'mini_player', state })
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'mini_player', state }), m)
  // null hides the bar
  assert.deepEqual(parseShellMessage('{"v":1,"kind":"mini_player","state":null}'), { kind: 'mini_player', state: null })
  // optional fields may be null or missing
  const bare = { ...state, subtitle: null, image: null, duration_ms: null }
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'mini_player', state: bare })?.state, bare)
})

test('anything else is ignored: other versions, kinds, wrong types', () => {
  for (const bad of [
    '', 'not json', '[]', '{"v":2,"kind":"mini_player","state":null}', '{"v":1,"kind":"something_new"}',
    { v: 1, kind: 'mini_player' },
    { v: 1, kind: 'mini_player', state: { ...state, playing: 'yes' } },
    { v: 1, kind: 'mini_player', state: { ...state, position_ms: -1 } },
    { v: 1, kind: 'mini_player', state: { ...state, position_ms: 1.5 } },
    { v: 1, kind: 'mini_player', state: { ...state, duration_ms: '3000' } },
    { v: 1, kind: 'mini_player', state: { ...state, id: '' } },
    { v: 1, kind: 'mini_player', state: { ...state, has_next: undefined } },
    'x'.repeat(20_000),
  ]) assert.equal(parseShellMessage(bad), null, JSON.stringify(bad).slice(0, 80))
})

test('artwork is only a web address or a path of this site', () => {
  const art = (image) => parseShellMessage({ v: 1, kind: 'mini_player', state: { ...state, image } })?.state?.image
  assert.equal(art('/api/v1/images/x'), '/api/v1/images/x')
  assert.equal(art('http://hub.example/a.jpg'), 'http://hub.example/a.jpg')
  assert.equal(art('javascript:alert(1)'), null)
  assert.equal(art('data:image/png;base64,AAAA'), null)
  assert.equal(art('//elsewhere.example/a.jpg'), null)
})

test('commands to the TV are one envelope each; only a seek carries a delta', () => {
  assert.deepEqual(JSON.parse(miniPlayerCommand('play_pause')), { v: 1, kind: 'mini_player_command', command: 'play_pause', delta_ms: null })
  assert.deepEqual(JSON.parse(miniPlayerCommand('seek_by', -10_000)), { v: 1, kind: 'mini_player_command', command: 'seek_by', delta_ms: -10_000 })
  assert.deepEqual(JSON.parse(miniPlayerCommand('seek_by', 30_000.7)).delta_ms, 30_000)
  for (const c of ['next', 'previous', 'expand', 'close']) assert.equal(JSON.parse(miniPlayerCommand(c, 5)).delta_ms, null)
})
