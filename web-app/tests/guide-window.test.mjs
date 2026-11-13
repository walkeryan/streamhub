import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SLOT_MS, cellBox, extendedEnd, mergeProgrammes, nearEnd, openingSpan, programmeAt, rowWindow, rowsToDraw, spanChunks } from '../src/lib/guideWindow.ts'

const HOUR = 3600_000
const at = (h) => new Date(Date.UTC(2030, 0, 1) + h * HOUR).toISOString()
const prog = (id, from, to) => ({ id, start: at(from), stop: at(to) })

test('the row window is the view plus a buffer, clamped to the list', () => {
  // 60 px rows, a 600 px view at the top: rows 0-9 in view, four more below.
  assert.deepEqual(rowWindow(0, 600, 60, 1000, 4), { first: 0, end: 14 })
  // Scrolled half a row into row 100: 100-110 in view.
  assert.deepEqual(rowWindow(6030, 600, 60, 1000, 4), { first: 96, end: 115 })
  assert.deepEqual(rowWindow(59_400, 600, 60, 1000, 4), { first: 986, end: 1000 })
  // A list shorter than the view, an empty one, and a view not measured yet.
  assert.deepEqual(rowWindow(0, 600, 60, 3, 4), { first: 0, end: 3 })
  assert.deepEqual(rowWindow(0, 600, 60, 0, 4), { first: 0, end: 0 })
  assert.deepEqual(rowWindow(0, 0, 60, 50, 4), { first: 0, end: 4 })
  // Scrolled past the end (the list shrank under the view): the last row is still drawn.
  assert.deepEqual(rowWindow(9000, 600, 60, 5, 0), { first: 4, end: 5 })
})

test('the drawn rows stay bounded however long the list is', () => {
  for (const count of [40, 1000, 52_000]) {
    for (const top of [0, 1234, count * 30, count * 60]) {
      const win = rowWindow(top, 600, 60, count, 4)
      assert.ok(rowsToDraw(win, null, count).length <= 19)
      assert.ok(rowsToDraw(win, 0, count).length <= 22)
    }
  }
})

test('the focused row and its neighbours are drawn wherever the view is', () => {
  const win = { first: 96, end: 115 }
  assert.deepEqual(rowsToDraw(win, null, 1000), Array.from({ length: 19 }, (_, i) => 96 + i))
  // Focus left behind above the view, in order before the window.
  assert.deepEqual(rowsToDraw(win, 10, 1000).slice(0, 4), [9, 10, 11, 96])
  // Focus below the view, after it.
  assert.deepEqual(rowsToDraw(win, 500, 1000).slice(-4), [114, 499, 500, 501])
  // Straddling the window's edge: no row twice.
  assert.deepEqual(rowsToDraw(win, 95, 1000).slice(0, 3), [94, 95, 96])
  assert.deepEqual(rowsToDraw(win, 115, 1000).slice(-3), [114, 115, 116])
  assert.equal(new Set(rowsToDraw(win, 96, 1000)).size, rowsToDraw(win, 96, 1000).length)
  // The first and the last row have one neighbour.
  assert.deepEqual(rowsToDraw({ first: 5, end: 8 }, 0, 1000), [0, 1, 5, 6, 7])
  assert.deepEqual(rowsToDraw({ first: 5, end: 8 }, 999, 1000), [5, 6, 7, 998, 999])
})

test('more channels are asked for a few rows before the end', () => {
  assert.equal(nearEnd(10, 40, 8), false)
  assert.equal(nearEnd(31, 40, 8), true)
  assert.equal(nearEnd(39, 40, 8), true)
  assert.equal(nearEnd(0, 0, 8), false)
  // Fewer rows than the margin: the first page already is near its end.
  assert.equal(nearEnd(0, 5, 8), true)
})

test('the guide opens on the half hour, three hours back and nine ahead', () => {
  const now = Date.parse(at(17.3))
  assert.deepEqual(openingSpan(now, 3, 9), { from: Date.parse(at(14)), to: Date.parse(at(26)) })
  assert.equal(openingSpan(now, 3, 9).from % SLOT_MS, 0)
})

test('the window grows by a step near its end and stops at the horizon', () => {
  const span = { from: Date.parse(at(14)), to: Date.parse(at(26)) }, horizon = Date.parse(at(41))
  assert.equal(extendedEnd(span, Date.parse(at(20)), 4, horizon), span.to)
  assert.equal(extendedEnd(span, Date.parse(at(25)), 4, horizon), Date.parse(at(30)))
  assert.equal(extendedEnd(span, Date.parse(at(26)), 4, horizon), Date.parse(at(30)))
  assert.equal(extendedEnd({ ...span, to: Date.parse(at(38)) }, Date.parse(at(38)), 4, horizon), horizon)
  assert.equal(extendedEnd({ ...span, to: horizon }, horizon, 4, horizon), horizon)
})

test('a longer window keeps the requests it already made', () => {
  const from = Date.parse(at(14))
  const first = spanChunks({ from, to: Date.parse(at(26)) }, 12, 4)
  assert.deepEqual(first, [{ from, to: Date.parse(at(26)) }])
  const grown = spanChunks({ from, to: Date.parse(at(34)) }, 12, 4)
  assert.deepEqual(grown, [first[0], { from: Date.parse(at(26)), to: Date.parse(at(30)) }, { from: Date.parse(at(30)), to: Date.parse(at(34)) }])
  // The horizon cuts the last request short; the ones before it are the same.
  const capped = spanChunks({ from, to: Date.parse(at(41)) }, 12, 4)
  assert.deepEqual(capped.slice(0, 3), grown)
  assert.deepEqual(capped.at(-1), { from: Date.parse(at(38)), to: Date.parse(at(41)) })
  assert.deepEqual(spanChunks({ from, to: from }, 12, 4), [])
})

test('programmes from adjoining requests merge without repeats, in time order', () => {
  const a = [prog('a', 14, 15), prog('b', 15, 16.5)], b = [prog('b', 15, 16.5), prog('c', 16.5, 17)]
  assert.deepEqual(mergeProgrammes([a, b]).map((p) => p.id), ['a', 'b', 'c'])
  assert.deepEqual(mergeProgrammes([b, a]).map((p) => p.id), ['a', 'b', 'c'])
  assert.deepEqual(mergeProgrammes([a]), a)
  assert.deepEqual(mergeProgrammes([]), [])
})

test('a cell is placed in minutes from the window start and clipped to the window', () => {
  const span = { from: Date.parse(at(14)), to: Date.parse(at(26)) }
  assert.deepEqual(cellBox(prog('a', 15, 16.5), span), { left: 60, width: 90 })
  assert.deepEqual(cellBox(prog('a', 13, 14.5), span), { left: 0, width: 30 })
  assert.deepEqual(cellBox(prog('a', 25.5, 27), span), { left: 690, width: 30 })
  assert.equal(cellBox(prog('a', 12, 14), span), null)
  assert.equal(cellBox(prog('a', 26, 27), span), null)
})

test('Up and Down land on the programme on at the same time', () => {
  const list = [prog('a', 14, 15), prog('b', 15, 16.5), prog('c', 18, 19)]
  const pick = (h) => programmeAt(list, Date.parse(at(h)))?.id
  assert.equal(pick(14.5), 'a')
  // A programme that ends at that moment is over: the next one is on.
  assert.equal(pick(15), 'b')
  // A gap in the schedule: the next to start. Past the end: the last. Before the start: the first.
  assert.equal(pick(17), 'c')
  assert.equal(pick(20), 'c')
  assert.equal(pick(10), 'a')
  assert.equal(programmeAt([], 0), null)
})
