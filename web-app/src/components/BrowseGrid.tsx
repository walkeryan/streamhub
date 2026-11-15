import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useBrowseJumps, useBrowsePages, type BrowseFilters } from '../api/hooks'
import type { Tile } from '../api/types'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useHistoryState } from '../nav/historyState'
import { Button, ErrorState, Skeleton } from './ui'
import { MediaTile } from './Tile'

// Start loading the next (or previous) page this far before the viewer reaches the end of what is loaded.
const AHEAD = '1200px 0px'

/** Calls `onHit` whenever the returned element comes within `AHEAD` of the viewport. */
function useSentinel(onHit: () => void, active: boolean) {
  const ref = useRef<HTMLDivElement | null>(null)
  const hit = useRef(onHit)
  hit.current = onHit
  useEffect(() => {
    const el = ref.current
    if (!active || !el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) hit.current() }, { rootMargin: AHEAD })
    io.observe(el)
    return () => io.disconnect()
  }, [active])
  return ref
}

/** Index of the first child whose bottom is below `top`: the tile at the top of the screen. */
function firstVisible(grid: HTMLElement, top: number): number {
  const kids = grid.children
  let lo = 0
  let hi = kids.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if ((kids[mid] as HTMLElement).getBoundingClientRect().bottom > top) hi = mid
    else lo = mid + 1
  }
  return lo
}

/**
 * The browse grid: loads as it scrolls, both ways, with no page buttons. Beside it a rail of jumps for the
 * current sort (A-Z, years, ratings, months) moves straight to where each starts, loading that page first.
 */
export function BrowseGrid(p: {
  section: string
  filters: BrowseFilters
  filterKey: string
  enabled: boolean
  empty: ReactNode
  onTotal?: (total: number | null, fetching: boolean) => void
}) {
  const [start, setStart] = useHistoryState(`browse:${p.section}:start`, 1)
  const [startKey, setStartKey] = useState(p.filterKey)
  if (startKey !== p.filterKey) { setStartKey(p.filterKey); setStart(1) }
  const q = useBrowsePages(p.section, p.filters, start, p.enabled)
  const jumps = useBrowseJumps(p.section, p.filters, p.enabled)
  const pages = useMemo(() => q.data?.pages ?? [], [q.data])
  const firstPage = pages[0]?.page ?? start
  const pageSize = pages[0]?.page_size ?? jumps.data?.page_size ?? 60
  const base = (firstPage - 1) * pageSize
  const total = pages[0]?.total ?? null
  // A title that moved between pages while they loaded is shown once.
  const tiles = useMemo(() => {
    const seen = new Set<string>()
    const out: Tile[] = []
    for (const page of pages) for (const t of page.tiles ?? []) if (!seen.has(t.id)) { seen.add(t.id); out.push(t) }
    return out
  }, [pages])
  const { onTotal } = p
  useEffect(() => { onTotal?.(total, q.isFetching) }, [onTotal, total, q.isFetching])

  const { hasNextPage, hasPreviousPage, isFetchingNextPage, isFetchingPreviousPage, isFetchNextPageError, fetchNextPage, fetchPreviousPage } = q
  const bottom = useSentinel(() => { if (hasNextPage && !isFetchingNextPage && !isFetchNextPageError) void fetchNextPage() }, !!hasNextPage && !isFetchNextPageError)
  const top = useSentinel(() => { if (hasPreviousPage && !isFetchingPreviousPage) void fetchPreviousPage() }, !!hasPreviousPage)

  // Earlier titles arriving above must not push the ones being looked at down the screen.
  const height = useRef(0)
  const shownFrom = useRef(firstPage)
  useLayoutEffect(() => {
    const now = document.documentElement.scrollHeight
    if (firstPage < shownFrom.current && height.current) window.scrollBy(0, now - height.current)
    shownFrom.current = firstPage
    height.current = now
  }, [firstPage, tiles.length])

  const main = useRef<HTMLDivElement | null>(null)
  const grid = useCallback(() => main.current?.querySelector<HTMLElement>('.grid-tiles') ?? null, [])
  const [at, setAt] = useState(0)
  // Right after a jump the rail names where it went, even when that row cannot reach the top of the screen.
  const jumpedUntil = useRef(0)
  useEffect(() => {
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      if (Date.now() < jumpedUntil.current) return
      frame = requestAnimationFrame(() => { const g = grid(); if (g?.children.length) setAt(base + firstVisible(g, 120)) })
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', onScroll) }
  }, [base, tiles.length, grid])

  // A jump to a title not loaded yet restarts the grid at its page; it is focused once that page is here.
  const pending = useRef<number | null>(null)
  const land = useCallback((offset: number) => {
    const t = tiles[offset - base]
    const el = grid()?.children[offset - base] as HTMLElement | undefined
    if (!t || !el) return false
    jumpedUntil.current = Date.now() + 600
    setAt(offset)
    el.scrollIntoView({ block: 'center' })
    void setFocus(`browse::${t.id}`)
    return true
  }, [tiles, base, grid])
  useEffect(() => { if (pending.current !== null && land(pending.current)) pending.current = null }, [land])
  const jump = (offset: number) => {
    if (land(offset)) return
    pending.current = offset
    window.scrollTo({ top: 0 })
    setStart(Math.floor(offset / pageSize) + 1)
  }

  const rail = (jumps.data?.jumps ?? []).filter((j) => total === null || j.offset < total)
  const current = rail.reduce<string | null>((label, j) => (j.offset <= at ? j.label : label), rail[0]?.label ?? null)

  if (q.error) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data || q.isPlaceholderData) return <div className="grid-tiles">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} style={{ aspectRatio: '2/3' }} />)}</div>
  if (!tiles.length) return <>{p.empty}</>
  return <div className="browse-grid-wrap">
    <div className="browse-grid-main" ref={main}>
      {hasPreviousPage && <div ref={top} className="browse-sentinel" aria-hidden="true" />}
      {isFetchingPreviousPage && <p className="label py-2" role="status">Loading earlier titles…</p>}
      <FocusGroup focusKey="browse:grid" className="grid-tiles">{tiles.map((t) => <MediaTile key={t.id} tile={t} scope="browse" />)}</FocusGroup>
      {hasNextPage && <div ref={bottom} className="browse-sentinel" aria-hidden="true" />}
      <div className="py-4" aria-live="polite">
        {isFetchNextPageError ? <p className="label">More titles could not be loaded. <Button focusKey="browse:retry-more" className="btn-chip" onEnter={() => void fetchNextPage()}>Try again</Button></p>
          : isFetchingNextPage ? <p className="label" role="status">Loading more titles…</p> : null}
      </div>
    </div>
    {rail.length > 1 && <FocusGroup focusKey="browse:jumps" className="browse-jumps" role="navigation" label="Jump to">
      {rail.map((j) => <Focusable key={j.label} focusKey={`browse:jump:${j.label}`} className="browse-jump" role="button" label={`Jump to ${j.label}`}
        aria-current={current === j.label ? true : undefined} data-testid="browse-jump" onEnter={() => jump(j.offset)} scroll="nearest">{j.label}</Focusable>)}
    </FocusGroup>}
  </div>
}
