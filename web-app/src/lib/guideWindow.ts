// The programme guide's arithmetic: which rows are drawn, when more channels or hours are asked for,
// and which cell an arrow lands on. No DOM and no React, so it is tested on its own (tests/guide-window.test.mjs).

export const SLOT_MS = 30 * 60_000
const HOUR_MS = 3600_000

/** Rows `first` up to, not including, `end`. */
export interface RowWindow { first: number; end: number }
export interface TimeSpan { from: number; to: number }
interface Timed { id: string; start: string; stop: string }

/** The rows in view plus `buffer` either side: every row is `rowHeight` tall, so this is all windowing takes. */
export function rowWindow(scrollTop: number, viewHeight: number, rowHeight: number, count: number, buffer: number): RowWindow {
  if (count <= 0 || rowHeight <= 0) return { first: 0, end: 0 }
  const first = Math.max(0, Math.min(count - 1, Math.floor(scrollTop / rowHeight) - buffer))
  const end = Math.min(count, Math.max(first + 1, Math.ceil((scrollTop + Math.max(0, viewHeight)) / rowHeight) + buffer))
  return { first, end }
}

/**
 * The rows to draw, in order: the window, and the focused row with the one above and below it wherever
 * the wheel has taken the view. A focused row that was recycled would drop focus, and Up or Down need
 * their target drawn before focus can move to it.
 */
export function rowsToDraw(win: RowWindow, focused: number | null, count: number): number[] {
  const rows: number[] = []
  const kept = focused === null ? [] : [focused - 1, focused, focused + 1].filter((i) => i >= 0 && i < count)
  for (const i of kept) if (i < win.first) rows.push(i)
  for (let i = win.first; i < win.end; i++) rows.push(i)
  for (const i of kept) if (i >= win.end) rows.push(i)
  return rows
}

/** The viewer has reached (by scrolling or by focus) within `near` rows of the last one loaded. */
export const nearEnd = (reached: number, loaded: number, near: number): boolean => loaded > 0 && reached >= loaded - 1 - near

/** The window the guide opens on: from `backHours` before now, on the half hour, to `forwardHours` after it. */
export function openingSpan(now: number, backHours: number, forwardHours: number): TimeSpan {
  const from = Math.floor((now - backHours * HOUR_MS) / SLOT_MS) * SLOT_MS
  return { from, to: from + (backHours + forwardHours) * HOUR_MS }
}

/** Where the window ends once the viewer has `reached` within an hour of its end: `stepHours` later, never past `horizon`. */
export function extendedEnd(span: TimeSpan, reached: number, stepHours: number, horizon: number): number {
  if (reached < span.to - HOUR_MS || span.to >= horizon) return span.to
  return Math.min(horizon, span.to + stepHours * HOUR_MS)
}

/**
 * The requests that cover a window: the opening one, then one per extension. Their edges depend only
 * on where the window starts, so growing it adds a request and leaves the earlier ones (and their cache) alone.
 */
export function spanChunks(span: TimeSpan, firstHours: number, stepHours: number): TimeSpan[] {
  const chunks: TimeSpan[] = []
  for (let from = span.from, size = firstHours * HOUR_MS; from < span.to && size > 0; size = stepHours * HOUR_MS) {
    const to = Math.min(span.to, from + size)
    chunks.push({ from, to })
    from = to
  }
  return chunks
}

/** One channel's programmes from several requests: a programme that crosses a request's edge is in both. */
export function mergeProgrammes<T extends Timed>(lists: ReadonlyArray<readonly T[]>): T[] {
  if (lists.length === 1) return [...lists[0]!]
  const byId = new Map<string, T>()
  for (const list of lists) for (const p of list) byId.set(p.id, p)
  return [...byId.values()].sort((a, b) => Date.parse(a.start) - Date.parse(b.start))
}

/** A programme's place in the window, in minutes from its start and clipped to it; null when it lies outside. */
export function cellBox(p: Pick<Timed, 'start' | 'stop'>, span: TimeSpan): { left: number; width: number } | null {
  const start = Math.max(Date.parse(p.start), span.from), stop = Math.min(Date.parse(p.stop), span.to)
  return stop > start ? { left: (start - span.from) / 60_000, width: (stop - start) / 60_000 } : null
}

/**
 * The cell Up or Down lands on in the next row: the programme on at `at`, else the first to start
 * after it, else the last before it. `list` is in time order.
 */
export function programmeAt<T extends Pick<Timed, 'start' | 'stop'>>(list: readonly T[], at: number): T | null {
  for (const p of list) if (Date.parse(p.stop) > at) return p
  return list[list.length - 1] ?? null
}
