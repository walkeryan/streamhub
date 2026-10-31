import { useEffect, useMemo, useRef, useState } from 'react'
import { useChannelGroups, useChannels, useSources, useToggleFavorite } from '../api/hooks'
import type { Channel } from '../api/types'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { CatalogPicker } from '../components/CatalogPicker'
import { LanguageFilter } from '../components/LanguageFilter'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useHistoryState } from '../nav/historyState'
import { useGo, useRestoreFocus } from '../nav/back'
import { Preview } from '../player/Preview'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { fmtTime, useDebounced } from '../lib/util'

const PAGE = 80

export function LiveTV() {
  const profile = useSession((s) => s.profile)
  const [lang, setLang] = useHistoryState('live:lang', profile?.languages.length ? 'mine' : 'all')
  const [group, setGroup] = useHistoryState('live:group', '')
  const [source, setSource] = useHistoryState('live:source', '')
  const [favOnly, setFavOnly] = useHistoryState('live:favOnly', false)
  const [search, setSearch] = useHistoryState('live:search', '')
  const term = useDebounced(search, 300)
  const [page, setPage] = useHistoryState('live:page', 0)
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const [previewEnabled, setPreviewEnabled] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const language = lang === 'mine' ? profile?.languages.join(',') || 'all' : lang
  const filterKey = `${language}|${group}|${source}|${favOnly}|${term}`
  const [pageKey, setPageKey] = useState(filterKey)
  // Reset paging synchronously with filters so no request briefly targets an old offset.
  if (pageKey !== filterKey) { setPageKey(filterKey); setPage(0) }
  const filters = { language, source_id: source || undefined, favorites: favOnly || undefined, q: term || undefined }
  const q = useChannels({ ...filters, group: group || undefined, limit: PAGE, offset: page * PAGE })
  const groups = useChannelGroups(filters)
  const sources = useSources()
  const channels = useMemo(() => q.data?.channels ?? [], [q.data])
  const total = q.data?.total ?? channels.length
  const ready = !!q.data && !q.isPlaceholderData
  const focused = channels.find((c) => c.id === focusedId) ?? null
  const setChannelList = useUi((s) => s.setChannelList)
  const toggleFav = useToggleFavorite()
  const go = useGo()
  useEffect(() => { if (ready && q.data) setChannelList(q.data.list_id ?? null, channels) }, [ready, q.data, channels, setChannelList])
  useRestoreFocus(ready, 'live:search')
  useEffect(() => {
    if (!previewEnabled || !focused) { setPreviewId(null); return }
    const id = window.setTimeout(() => setPreviewId(focused.id), 600)
    return () => window.clearTimeout(id)
  }, [focused, previewEnabled])

  // D-pad number entry searches the complete catalog, not just the current page.
  const digits = useRef('')
  useEffect(() => {
    let timer = 0
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t?.tagName === 'INPUT' || t?.tagName === 'TEXTAREA' || !/^[0-9]$/.test(e.key) || e.ctrlKey || e.metaKey || e.altKey) return
      digits.current = (digits.current + e.key).slice(-6)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => { setSearch(digits.current); digits.current = ''; void setFocus('live:search') }, 700)
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(timer) }
  }, [setSearch])
  const playChannel = (c: Channel) => {
    setChannelList(q.data?.list_id ?? null, channels)
    go(`/play/${encodeURIComponent(c.id)}${q.data?.list_id ? `?list=${encodeURIComponent(q.data.list_id)}` : ''}`)
  }
  const reset = () => { setGroup(''); setSource(''); setFavOnly(false); setSearch(''); setPage(0) }
  const changePage = (p: number) => { setPage(p); setPreviewId(null); setFocusedId(null); void setFocus('live:search') }
  const groupOptions = [{ id: '', label: 'All categories' }, ...(groups.data ?? []).filter((g) => !g.hidden).map((g) => ({ id: g.id, label: g.name, count: g.channel_count }))]
  const sourceOptions = [{ id: '', label: 'All sources' }, ...(sources.data ?? []).filter((s) => s.type === 'm3u' || s.type === 'xtream').map((s) => ({ id: s.id, label: s.name }))]

  return <div className="page-pad" data-testid="live" style={{ paddingBottom: '2rem' }}>
    <div className="flex items-baseline gap-4 mb-3"><h1 className="display" style={{ fontSize: 'var(--text-title)' }}>Live TV</h1><p className="label" aria-live="polite">{q.isFetching ? 'Updating…' : `${total.toLocaleString()} channels`}</p></div>
    <FocusGroup focusKey="live:filters" className="flex flex-col gap-3 mb-4">
      <LanguageFilter scope="live" value={lang} onChange={(v) => { setLang(v); setGroup('') }} />
      <div className="flex gap-2 flex-wrap items-center">
        <div style={{ width: 'min(24rem, 100%)' }}><FocusableInput focusKey="live:search" label="Search channels or number" placeholder="Channel name or number" value={search} onChange={setSearch} inputMode="search" /></div>
        <CatalogPicker scope="live:category" label="Category" value={group} options={groupOptions} onChange={setGroup} />
        {sourceOptions.length > 2 && <CatalogPicker scope="live:source" label="Source" value={source} options={sourceOptions} onChange={(v) => { setSource(v); setGroup('') }} />}
        <Chip focusKey="live:fav" pressed={favOnly} onEnter={() => setFavOnly((v) => !v)} testId="live-fav-filter">Favorites only</Chip>
        {(group || source || favOnly || search) && <Button focusKey="live:reset" ghost onEnter={reset}>Clear filters</Button>}
      </div>
      {groups.error && <p role="status" className="muted">Categories could not load. You can still search channels.</p>}
    </FocusGroup>
    <div className="live-catalog-layout grid gap-6">
      <div>
        {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : !ready ? <div className="flex flex-col gap-2">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} style={{ height: '3.5rem' }} />)}</div> : !channels.length ?
          <Empty title="No channels match" hint="Clear the category or search, or choose All languages to include other and unknown languages."><Button focusKey="live:empty-reset" onEnter={reset}>Clear filters</Button><Button focusKey="live:empty-all" onEnter={() => { reset(); setLang('all') }}>All languages</Button></Empty> :
          <FocusGroup focusKey="live:list" className="flex flex-col gap-2" role="listbox" label="Channels" remember>
            {channels.map((c) => <ChannelRow key={c.id} focusKey={`live::${c.id}`} channel={c} onEnter={() => playChannel(c)} onFocus={() => setFocusedId(c.id)} />)}
          </FocusGroup>}
        <FocusGroup focusKey="live:pages" className="flex gap-3 flex-wrap items-center py-4" label="Channel pages">
          <Button focusKey="live:previous" disabled={!page || q.isFetching} onEnter={() => changePage(page - 1)}>Previous page</Button>
          <span className="label">{total ? `${(page * PAGE + 1).toLocaleString()}–${Math.min((page + 1) * PAGE, total).toLocaleString()} of ${total.toLocaleString()}` : '0 channels'}</span>
          <Button focusKey="live:next" disabled={(page + 1) * PAGE >= total || q.isFetching} onEnter={() => changePage(page + 1)}>Next page</Button>
        </FocusGroup>
      </div>
      <aside className="live-catalog-preview flex flex-col gap-3" aria-label="Preview" style={{ position: 'sticky', top: '1rem', alignSelf: 'start' }}>
        <Preview channelId={previewId} />
        {focused && <div className="flex flex-col gap-1" data-testid="live-info"><h2 className="title-xl">{focused.number} · {focused.name}</h2><p>{focused.now ? `Now: ${focused.now.title} (${fmtTime(focused.now.start)} to ${fmtTime(focused.now.stop)})` : 'No programme data supplied for this channel'}</p>{focused.next && <p className="muted">Next: {focused.next.title} at {fmtTime(focused.next.start)}</p>}</div>}
        <FocusGroup focusKey="live:actions" className="flex gap-2 flex-wrap">
          <Button focusKey="live:watch" primary icon="play" disabled={!focused} onEnter={() => focused && playChannel(focused)}>Watch full screen</Button>
          <Chip focusKey="live:preview" pressed={previewEnabled} onEnter={() => setPreviewEnabled((v) => !v)}>Preview {previewEnabled ? 'on' : 'off'}</Chip>
          <Button focusKey="live:togglefav" icon="heart" disabled={!focused} pressed={!!focused?.is_favorite} onEnter={() => focused && toggleFav.mutate({ kind: 'channel', id: focused.id, on: !focused.is_favorite })}>{focused?.is_favorite ? 'Remove favorite' : 'Add favorite'}</Button>
        </FocusGroup>
      </aside>
    </div>
  </div>
}
