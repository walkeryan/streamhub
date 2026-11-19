import { test } from 'node:test'
import assert from 'node:assert/strict'
import { keepClearMessage, miniPlayerCommand, overlaps, parseShellMessage, scrollClearOf } from '../src/shellProtocol.ts'

const state = {
  id: 'lib:plex:72', title: 'The Lighthouse Keepers', subtitle: 'S1 · E2 · The Storm', image: 'https://hub.example/api/v1/images/abc',
  position_ms: 61_000, duration_ms: 3_000_000, playing: true, has_next: true, has_previous: false, live: false,
}

test('the TV reports what plays in picture-in-picture, as text or already parsed', () => {
  const m = parseShellMessage(JSON.stringify({ v: 1, kind: 'mini_player', state }))
  // An older TV sends neither keep_clear nor pip_rect: no keep-clear areas, window unknown.
  assert.deepEqual(m, { kind: 'mini_player', state: { ...state, keep_clear: false, pip_rect: null } })
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'mini_player', state }), m)
  // null hides the bar
  assert.deepEqual(parseShellMessage('{"v":1,"kind":"mini_player","state":null}'), { kind: 'mini_player', state: null })
  // optional fields may be null or missing
  const bare = { ...state, subtitle: null, image: null, duration_ms: null }
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'mini_player', state: bare })?.state, { ...bare, keep_clear: false, pip_rect: null })
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

test('a newer TV says it takes keep-clear areas and where its window is; a bad rect reads as unknown', () => {
  const pip_rect = { x: 1400, y: 60, w: 480, h: 270 }
  assert.deepEqual(parseShellMessage({ v: 1, kind: 'mini_player', state: { ...state, keep_clear: true, pip_rect } })?.state, { ...state, keep_clear: true, pip_rect })
  for (const bad of [{ x: 1, y: 2, w: 0, h: 3 }, { x: 'a', y: 2, w: 3, h: 3 }, { x: 1, y: 2, w: 1e9, h: 3 }, 'rect']) {
    assert.equal(parseShellMessage({ v: 1, kind: 'mini_player', state: { ...state, pip_rect: bad } })?.state?.pip_rect, null)
  }
})

test('the page tells the TV what its window should stay off, rounded, at most eight rects', () => {
  const msg = JSON.parse(keepClearMessage([{ x: 10.4, y: 20.6, w: 300.5, h: -4 }], 1920.2))
  assert.deepEqual(msg, { v: 1, kind: 'keep_clear', rects: [{ x: 10, y: 21, w: 301, h: 0 }], viewport_width: 1920 })
  const many = Array.from({ length: 12 }, (_, i) => ({ x: i, y: i, w: 10, h: 10 }))
  assert.equal(JSON.parse(keepClearMessage(many, 1920)).rects.length, 8)
})

test('without keep-clear, the focused item scrolls out from under the window', () => {
  const top = { x: 1400, y: 60, w: 480, h: 270 }
  assert.equal(overlaps({ x: 0, y: 0, w: 100, h: 100 }, top), false)
  // A tile under a window in the top corner is pushed below it (scroll up by the overlap plus a gap).
  assert.equal(scrollClearOf({ x: 1500, y: 200, w: 300, h: 170 }, top, 1080), -(60 + 270 + 16 - 200))
  // Under a window in the bottom corner, it is pulled above it.
  const bottom = { x: 1400, y: 760, w: 480, h: 270 }
  assert.equal(scrollClearOf({ x: 1500, y: 800, w: 300, h: 170 }, bottom, 1080), 800 + 170 - (760 - 16))
  assert.equal(scrollClearOf({ x: 0, y: 800, w: 300, h: 170 }, bottom, 1080), 0)
})
