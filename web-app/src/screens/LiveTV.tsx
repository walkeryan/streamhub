import { useEffect, useMemo, useRef, useState } from 'react'
import { useChannelGroups, useChannels, useLanguages, useToggleFavorite } from '../api/hooks'
import type { Channel } from '../api/types'
import { Button, Chip, Empty, ErrorState, Skeleton } from '../components/ui'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useGo, useRestoreFocus } from '../nav/back'
import { Preview } from '../player/Preview'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { LANG_NAMES, fmtTime } from '../lib/util'

const PAGE = 120

export function LiveTV() {
  const profile = useSession((s) => s.profile)
  const [lang, setLang] = useState<string>(profile?.languages.length ? 'mine' : 'all')
  const [group, setGroup] = useState<string | undefined>()
  const [favOnly, setFavOnly] = useState(false)
  const [focused, setFocused] = useState<Channel | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [visible, setVisible] = useState(PAGE)
  const [digits, setDigits] = useState('')
  const langs = useLanguages()
  const groups = useChannelGroups()
  const setChannelList = useUi((s) => s.setChannelList)
  const toggleFav = useToggleFavorite()
  const go = useGo()

  const language = lang === 'mine' ? undefined : lang === 'all' ? langs.data?.map((l) => l.code).join(',') : lang
  const q = useChannels({ group, language, favorites: favOnly || undefined })
  const channels = useMemo(() => q.data?.channels ?? [], [q.data])
  const ready = !!q.data && (lang !== 'all' || !!langs.data)

  useEffect(() => { if (q.data) setChannelList(q.data.list_id ?? null, channels) }, [q.data, channels, setChannelList])
  useEffect(() => { setVisible(PAGE) }, [lang, group, favOnly])
  useRestoreFocus(ready && channels.length > 0, channels[0] ? `live::${channels[0].id}` : 'live:fav')

  // 600 ms dwell before the preview tunes
  useEffect(() => {
    if (!focused) return
    const id = window.setTimeout(() => setPreviewId(focused.id), 600)
    return () => window.clearTimeout(id)
  }, [focused])

  // number-key jump: digits commit after 2 s of silence (or on Enter handled by the row itself)
  const digitsRef = useRef('')
  useEffect(() => {
    let timer = 0
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
      if (!/^[0-9]$/.test(e.key) || e.ctrlKey || e.metaKey || e.altKey) return
      digitsRef.current = (digitsRef.current + e.key).slice(-4)
      setDigits(digitsRef.current)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const n = Number(digitsRef.current)
        digitsRef.current = ''
        setDigits('')
        const idx = channels.findIndex((c) => c.number === n)
        if (idx >= 0) {
          setVisible((v) => Math.max(v, idx + 20))
          window.setTimeout(() => void setFocus(`live::${channels[idx]!.id}`), 30)
        }
      }, 2000)
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(timer) }
  }, [channels])

  const more = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = more.current
    if (!el) return
    const io = new IntersectionObserver((e) => e[0]?.isIntersecting && setVisible((v) => v + PAGE), { rootMargin: '300px' })
    io.observe(el)
    return () => io.disconnect()
  }, [channels.length])

  const playChannel = (c: Channel) => {
    setChannelList(q.data?.list_id ?? null, channels)
    go(`/play/${encodeURIComponent(c.id)}${q.data?.list_id ? `?list=${encodeURIComponent(q.data.list_id)}` : ''}`)
  }
  const profileLangs = profile?.languages ?? []
  const langChips = langs.data ? [
    ...(profileLangs.length ? [{ id: 'mine', label: 'My languages' }] : []),
    { id: 'all', label: 'All' },
    ...[...langs.data].sort((a, b) => Number(profileLangs.includes(b.code)) - Number(profileLangs.includes(a.code))).map((l) => ({ id: l.code, label: `${LANG_NAMES[l.code] ?? l.name} ${l.count}` })),
  ] : []

  return (
    <div className="page-pad" data-testid="live" style={{ paddingBottom: '2rem' }}>
      <div className="flex items-baseline gap-4 mb-3">
        <h1 className="display" style={{ fontSize: 'var(--text-title)' }}>Live TV</h1>
        <p className="label" aria-live="polite">{q.data ? `${channels.length} channels` : ''}</p>
      </div>
      <FocusGroup focusKey="live:filters" className="flex flex-col gap-2 mb-3">
        <div className="flex gap-2 flex-wrap" role="group" aria-label="Language">
          {langChips.map((c) => <Chip key={c.id} focusKey={`live:lang:${c.id}`} pressed={lang === c.id} onEnter={() => setLang(c.id)}>{c.label}</Chip>)}
        </div>
        <div className="flex gap-2 flex-wrap" role="group" aria-label="Group">
          <Chip focusKey="live:fav" pressed={favOnly} onEnter={() => setFavOnly((v) => !v)} testId="live-fav-filter">Favorites only</Chip>
          <Chip focusKey="live:grp:all" pressed={!group} onEnter={() => setGroup(undefined)}>All groups</Chip>
          {(groups.data ?? []).filter((g) => !g.hidden).map((g) => (
            <Chip key={g.id} focusKey={`live:grp:${g.id}`} pressed={group === g.name} onEnter={() => setGroup(g.name)}>{g.name}</Chip>
          ))}
        </div>
      </FocusGroup>

      <div className="grid gap-6 max-md:grid-cols-1" style={{ gridTemplateColumns: 'minmax(0, 28rem) minmax(0, 1fr)' }}>
        <div>
          {q.error ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : !ready ? (
            <div className="flex flex-col gap-2">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} style={{ height: '3.5rem' }} />)}</div>
          ) : channels.length === 0 ? (
            <Empty title="No channels match" hint="Try All languages, another group, or turn off Favorites only.">
              <Button focusKey="live:reset" onEnter={() => { setLang('all'); setGroup(undefined); setFavOnly(false) }}>Reset filters</Button>
            </Empty>
          ) : (
            <FocusGroup focusKey="live:list" className="flex flex-col gap-2" role="listbox" label="Channels" remember>
              {channels.slice(0, visible).map((c) => (
                <ChannelRow key={c.id} focusKey={`live::${c.id}`} channel={c} onEnter={() => playChannel(c)} onFocus={() => setFocused(c)} />
              ))}
              <div ref={more} aria-hidden="true" style={{ height: 1 }} />
            </FocusGroup>
          )}
        </div>
        <aside className="max-md:hidden flex flex-col gap-3" style={{ position: 'sticky', top: '1rem', alignSelf: 'start' }} aria-label="Preview">
          <Preview channelId={previewId} />
          {focused && (
            <div className="flex flex-col gap-1" data-testid="live-info">
              <h2 className="title-xl">{focused.number} · {focused.name}</h2>
              <p>{focused.now ? `Now: ${focused.now.title} (${fmtTime(focused.now.start)} to ${fmtTime(focused.now.stop)})` : 'No programme data'}</p>
              {focused.next && <p className="muted">Next: {focused.next.title} at {fmtTime(focused.next.start)}</p>}
            </div>
          )}
          <FocusGroup focusKey="live:actions" className="flex gap-2 flex-wrap">
            <Button focusKey="live:watch" primary icon="play" disabled={!focused} onEnter={() => focused && playChannel(focused)}>Watch full screen</Button>
            <Button focusKey="live:togglefav" icon="heart" disabled={!focused} pressed={!!focused?.is_favorite}
              onEnter={() => focused && toggleFav.mutate({ kind: 'channel', id: focused.id, on: !focused.is_favorite }, { onSuccess: () => setFocused({ ...focused, is_favorite: !focused.is_favorite }) })}>
              {focused?.is_favorite ? 'Remove favorite' : 'Add favorite'}
            </Button>
          </FocusGroup>
        </aside>
      </div>
      {digits && <div className="numpad" role="status" aria-live="assertive" data-testid="digits" style={{ position: 'fixed' }}>{digits}</div>}
    </div>
  )
}
