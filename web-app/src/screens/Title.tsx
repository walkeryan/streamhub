import { useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { useTitle, useToggleFavorite, useToggleWatchlist, useWatchlist } from '../api/hooks'
import { reportProgress } from '../api/client'
import type { SourceBadge } from '../api/types'
import { Attribution, Button, Chip, Crossfade, ErrorState, Meta, Modal, Skeleton } from '../components/ui'
import { SafeImg } from '../components/SafeImg'
import { MediaTile } from '../components/Tile'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { usePlayAction } from '../components/playAction'
import { useAdaptiveAccent } from '../lib/theme'
import { useUi } from '../store/ui'

const KIND_LABEL: Record<string, string> = { movie: 'Movie', show: 'Series', series: 'Series', vod: 'Movie', episode: 'Episode', season: 'Season' }

export function TitleScreen() {
  const id = decodeURIComponent(useParams().id ?? '')
  const { data: t, error, refetch } = useTitle(id)
  const watchlist = useWatchlist()
  const toggleFav = useToggleFavorite()
  const toggleWatch = useToggleWatchlist()
  const play = usePlayAction()
  const toast = useUi((s) => s.toast)
  const [season, setSeason] = useState<string | null>(null)
  const [picker, setPicker] = useState<null | 'play'>(null)
  const seasonQ = useTitle(season ?? undefined)
  useAdaptiveAccent(t?.backdrop ?? t?.poster)
  useRestoreFocus(!!t, 'title:play')

  const { inApp, handoff } = useMemo(() => {
    const seen = new Set<string>()
    const uniq = (t?.sources ?? []).filter((s) => (seen.has(s.source_id + s.kind) ? false : (seen.add(s.source_id + s.kind), true)))
    return { inApp: uniq.filter((s) => s.playable_in_app), handoff: uniq.filter((s) => !s.playable_in_app) }
  }, [t])

  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  if (!t) return <div className="page-pad flex gap-8"><Skeleton style={{ width: '16rem', height: '24rem' }} /><div className="flex-1 flex flex-col gap-3"><Skeleton style={{ height: '3rem', width: '60%' }} /><Skeleton style={{ height: '8rem' }} /></div></div>

  const onWatchlist = (watchlist.data ?? []).some((w) => w.id === t.id)
  const left = t.progress_pct && t.runtime_min ? Math.max(1, Math.round(t.runtime_min * (1 - t.progress_pct / 100))) : null
  const canPlay = inApp.length > 0
  const playLabel = canPlay ? (left ? `Resume · ${left} min left` : 'Play') : 'Open in app'
  const episodes = season ? seasonQ.data?.episodes ?? [] : t.episodes ?? []
  const seasons = t.seasons ?? []

  const primary = () => {
    if (canPlay) return void play(t)
    if (handoff.length === 1) return void play(t, handoff[0])
    setPicker('play')
  }
  const choose = (s: SourceBadge) => { setPicker(null); void play(t, s) }
  const markWatched = () => {
    const dur = (t.runtime_min ?? 0) * 60_000
    reportProgress({ id: t.id, position_ms: dur, duration_ms: dur || null, state: 'stopped' })
    toast('Marked as watched.', 'success')
  }

  return (
    <div data-testid="title-detail" style={{ position: 'relative' }}>
      <div style={{ position: 'absolute', inset: '0 0 auto 0', height: 'min(70dvh, 44rem)', overflow: 'hidden' }} aria-hidden="true">
        <Crossfade src={t.backdrop ?? t.poster} />
        <div style={{ position: 'absolute', inset: 0, zIndex: 1, background: 'linear-gradient(to top, var(--t-bg) 6%, transparent 70%), linear-gradient(to right, var(--t-bg) 4%, transparent 70%)' }} />
      </div>
      <div className="page-pad" style={{ position: 'relative', zIndex: 2, paddingTop: 'max(var(--safe-y), 4rem)' }}>
        <div className="flex gap-8 flex-wrap md:flex-nowrap">
          <div className="tile tile-poster" style={{ flex: 'none', width: 'calc(var(--tile-poster-w) * 1.4)', height: 'calc(var(--tile-poster-h) * 1.4)', position: 'relative' }}>
            <SafeImg className="art" src={t.poster} alt="" fallback={<div className="grid place-items-center h-full p-4 text-center heading">{t.title}</div>} />
          </div>
          <div className="flex flex-col gap-4 min-w-0" style={{ maxWidth: '44rem' }}>
            <h1 className="display">{t.title}</h1>
            <Meta items={[KIND_LABEL[t.kind], t.year, t.runtime_min ? `${t.runtime_min} min` : null, t.genres?.join(', '), t.rating ? `★ ${t.rating.toFixed(1)}` : null]} />
            {t.overview && <p style={{ lineHeight: 1.55 }}>{t.overview}</p>}
            <FocusGroup focusKey="title:actions" className="flex gap-3 flex-wrap items-center" preferred="title:play">
              <Button focusKey="title:play" primary icon="play" onEnter={primary} testId="title-play">{playLabel}</Button>
              {inApp.length > 1 && <Button focusKey="title:source" icon="source" onEnter={() => setPicker('play')} testId="title-source">Choose source</Button>}
              <Button focusKey="title:watchlist" icon={onWatchlist ? 'check' : 'bookmark'} pressed={onWatchlist} onEnter={() => toggleWatch.mutate({ id: t.id, on: !onWatchlist })}>
                {onWatchlist ? 'On watchlist' : 'Watchlist'}
              </Button>
              <Button focusKey="title:fav" icon="heart" pressed={!!t.is_favorite} onEnter={() => toggleFav.mutate({ kind: t.kind, id: t.id, on: !t.is_favorite })}>
                {t.is_favorite ? 'Favorited' : 'Favorite'}
              </Button>
              <Button focusKey="title:trailer" icon="external" onEnter={() => window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(`${t.title} ${t.year ?? ''} trailer`)}`, '_blank', 'noopener')}>Trailer</Button>
              {canPlay && <Button focusKey="title:watched" icon="eye" onEnter={markWatched}>Mark watched</Button>}
            </FocusGroup>
            {t.cast && t.cast.length > 0 && <p className="label">Cast: {t.cast.map((c) => c.name).join(', ')}</p>}
          </div>
        </div>

        {seasons.length > 0 && (
          <section className="mt-8" aria-label="Seasons and episodes" data-testid="episodes">
            <FocusGroup focusKey="title:seasons" className="flex gap-2 flex-wrap mb-2">
              {seasons.map((s, i) => (
                <Chip key={s.id} focusKey={`season:${s.id}`} pressed={(season ?? seasons[0]?.id) === s.id} onEnter={() => setSeason(i === 0 ? null : s.id)}>{s.title}</Chip>
              ))}
            </FocusGroup>
            <FocusGroup focusKey="title:episodes" className="row-scroll" style={{ paddingInline: 0 }}>
              {episodes.map((e) => (
                <MediaTile key={e.id} tile={{ ...e, title: e.title }} scope="episodes" layout="landscape" titleAlways onOpen={() => void play(e)} />
              ))}
            </FocusGroup>
          </section>
        )}

        <section className="mt-6 flex flex-col gap-2" aria-label="Available on" data-testid="available-on">
          <h2 className="heading">Available on</h2>
          <FocusGroup focusKey="title:avail" className="flex gap-2 flex-wrap">
            {[...inApp, ...handoff].map((s) => (
              <Focusable key={s.source_id + s.kind} focusKey={`avail:${s.source_id}:${s.kind}`} className="chip fx-flat" onEnter={() => void play(t, s)}
                label={`${s.playable_in_app ? 'Play' : 'Open in app'}: ${s.name}`}>
                <span className={s.playable_in_app ? 'badge badge-play' : 'badge'}>{s.playable_in_app ? 'Play' : 'Open in app'}</span>
                {s.name}{s.quality ? ` ${s.quality}` : ''}{s.kind && !s.playable_in_app ? ` · ${s.kind}` : ''}
              </Focusable>
            ))}
            {!inApp.length && !handoff.length && <span className="muted">Not available from your sources.</span>}
          </FocusGroup>
          <Attribution text={t.attribution ?? 'Availability data by JustWatch'} />
        </section>
      </div>

      <Modal open={picker !== null} onClose={() => setPicker(null)} title={canPlay ? 'Play from' : 'Open in'} firstFocusKey="pick:0">
        <div className="flex flex-col gap-2">
          {(canPlay ? inApp : handoff).map((s, i) => (
            <Button key={s.source_id + s.kind} focusKey={`pick:${i}`} onEnter={() => choose(s)}>
              {s.name}{s.quality ? ` · ${s.quality}` : ''}
            </Button>
          ))}
          <Button focusKey="pick:cancel" ghost onEnter={() => setPicker(null)}>Cancel</Button>
        </div>
      </Modal>
    </div>
  )
}
