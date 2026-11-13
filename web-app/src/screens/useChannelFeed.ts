import { useEffect, useMemo } from 'react'
import { useChannelPages, type ChannelFilters } from '../api/hooks'
import { nearEnd, rowsToDraw, type RowWindow } from '../lib/guideWindow'
import { VIEW_BUFFER } from './useRowView'

const PAGE = 40
const NEAR = 8 // rows from the end at which the next page of channels is asked for

/** Live TV's channels for a view that just scrolls (the guide, the Channels list): asked for a page at a time, held as one list. */
export function useChannelFeed(filters: ChannelFilters) {
  const query = useChannelPages(filters, PAGE)
  const pages = query.data?.pages
  // A list that changed between two requests can repeat a channel: it keeps one row.
  const channels = useMemo(() => { const seen = new Set<string>(); return (pages ?? []).flatMap((p) => p.channels ?? []).filter((c) => !seen.has(c.id) && seen.add(c.id)) }, [pages])
  const rowOf = useMemo(() => new Map(channels.map((c, i) => [c.id, i])), [channels])
  const count = channels.length
  return { query, pages, channels, rowOf, count, total: pages?.[0]?.total ?? count, listId: pages?.[0]?.list_id ?? null }
}
export type ChannelFeed = ReturnType<typeof useChannelFeed>

/**
 * The rows to draw (the view's, and the focused one with its neighbours: see rowsToDraw), in order. The next
 * page is asked for when the view or the focus comes within a few rows of the last one loaded, and for as
 * long as Back wants a row further down than is loaded.
 */
export function useChannelRows(feed: ChannelFeed, view: { win: RowWindow; placed: boolean }, restoredRow: number | null, focusRow: number | null): number[] {
  const { pages, count } = feed
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = feed.query
  const first = Math.min(view.win.first, Math.max(0, count - 1)), end = Math.min(Math.max(view.win.end, first + 1), count)
  const drawn = useMemo(() => rowsToDraw({ first, end }, focusRow, count), [first, end, focusRow, count])
  const reached = Math.max(end - 1 - VIEW_BUFFER, focusRow ?? 0)
  const ready = view.placed && count > 0
  const restoring = !view.placed && restoredRow !== null && restoredRow >= count
  useEffect(() => {
    if (!pages || !hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    if (restoring || (ready && nearEnd(reached, count, NEAR))) void fetchNextPage()
  }, [pages, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage, restoring, ready, reached, count])
  return drawn
}

/** The last line of a list that loads as it scrolls: more is coming, or how much there was. */
export const channelsEnd = (more: boolean, total: number) => (more ? 'Loading more channels…' : `End of the list · ${total.toLocaleString()} channels`)
