import { HomeNotices } from '../components/Notices'
import { useContinueVisibility } from '../components/ContinueVisibility'
import { WatchingNow } from '../components/WatchingNow'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useHome } from '../api/hooks'
import type { Tile } from '../api/types'
import { Button, Crossfade, Empty, ErrorState, Skeleton, useLoadedImage } from '../components/ui'
import { GlassIconButton, Icon, SkeletonRow } from '../ds'
import { SafeImg } from '../components/SafeImg'
import { TileBadge, TileRow, namesSource } from '../components/Tile'
import { FocusGroup } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useProfilePreference } from '../api/profilePreferences'
import { useHomeRows } from '../api/viewingPreferences'
import { useAdaptiveAccent } from '../lib/theme'

export const isPlayable = (t: Tile) => t.kind === 'channel' || t.sources.some((s) => s.playable_in_app)

export function Home() {
  const { data, isLoading, error, refetch } = useHome()
  const visibility = useContinueVisibility()
  const { value: prefs } = useHomeRows()
  const go = useGo()
  const [idx, setIdx] = useState(0)
  const [driver, setDriver] = useState<Tile | null>(null)
  const [heroFocused, setHeroFocused] = useState(false)

  const [reduceMotion, setReduceMotion] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  const rotation = useProfilePreference<boolean>('featuredPaused.v1', false, (raw) => typeof raw === 'boolean' ? raw : null)
  const [allowMotion, setAllowMotion] = useState(false)
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    const change = () => { setReduceMotion(media.matches); setAllowMotion(false) }
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])
  const rotating = rotation.ready && !rotation.value && (!reduceMotion || allowMotion)
  const toggleRotation = () => {
    setAllowMotion(!rotating)
    rotation.update(rotating)
  }
  const hero = data?.hero ?? []
  const rows = useMemo(() => {
    const list = (data?.rows ?? []).filter((r) => !prefs.hidden.includes(r.id) && r.tiles.length > 0)
    const pos = (id: string) => { const i = prefs.order.indexOf(id); return i < 0 ? 1000 : i }
    return [...list].sort((a, b) => pos(a.id) - pos(b.id))
  }, [data, prefs])

  const active = driver ?? hero[idx % Math.max(1, hero.length)] ?? rows[0]?.tiles[0] ?? null
  useAdaptiveAccent(active?.backdrop ?? active?.poster)
  // with no usable backdrop the hero shrinks instead of leaving an empty band
  const art = useLoadedImage(active?.backdrop ?? active?.poster)
  const noArt = art.settled && !art.url

  // Auto-advance every 8 s; paused while a hero button is focused or a row tile is driving the hero.
  useEffect(() => {
    if (!rotating || hero.length < 2 || heroFocused || driver) return
    const id = window.setInterval(() => { if (!document.hidden) setIdx((i) => (i + 1) % hero.length) }, 8000)
    return () => window.clearInterval(id)
  }, [rotating, hero.length, heroFocused, driver])

  useRestoreFocus(!!data, 'hero:play')

  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  if (isLoading) return (
    <div data-testid="home-loading" className="home">
      <div className="home-hero"><Skeleton style={{ position: 'absolute', inset: 0, borderRadius: 0 }} /></div>
      <div className="home-rows"><SkeletonRow layout="landscape" count={5} /><SkeletonRow layout="poster" /></div>
    </div>
  )
  if (!active) return (
    <div className="page-pad">
      {visibility.notice}
      <Empty icon="source" title="Nothing to watch yet" hint="Connect Plex, Jellyfin, an IPTV provider or your streaming services and your library shows up here.">
        <Button focusKey="hero:play" primary icon="plus" onEnter={() => go('/sources')}>Add a source</Button>
      </Empty>
    </div>
  )

  const playable = isPlayable(active)
  const shown = idx % Math.max(1, hero.length)
  return (
    <div data-testid="home" className="home">
      <WatchingNow />
      <HomeNotices />
      {/* The billboard: art under the clear top bar, the title, one line of facts, Play and Details. */}
      <section className="home-hero" aria-label="Featured" data-testid="hero" data-art={noArt ? 'none' : undefined}>
        <Crossfade src={active.backdrop ?? active.poster} />
        <div className="home-hero-body">
          <h1 className="home-hero-title" data-testid="hero-title"><SafeImg key={active.id} src={active.logo} alt={active.title} fallback={active.title} /></h1>
          <p className="ds-meta">
            {active.year ? <span>{active.year}</span> : null}
            {active.subtitle ? <span>{active.subtitle}</span> : null}
            {active.rating ? <span className="ds-star"><b>★</b> {active.rating.toFixed(1)}</span> : null}
          </p>
          {/* The same rule as a tile: one ordinary source says nothing. */}
          {namesSource(active.sources) && <div className="flex gap-2 flex-wrap" data-testid="hero-sources"><TileBadge sources={active.sources} max={3} /></div>}
          <FocusGroup focusKey="hero" className="home-hero-actions" onFocusChange={setHeroFocused} remember={false} preferred="hero:play">
            <Button focusKey="hero:play" primary lg testId="hero-play"
              onEnter={() => go(playable ? `/play/${encodeURIComponent(active.id)}` : `/title/${encodeURIComponent(active.id)}`)}>
              <Icon name="play" />{playable ? 'Play' : 'Open in app'}
            </Button>
            <GlassIconButton focusKey="hero:details" icon="info" label="Details" testId="hero-details" onEnter={() => go(`/title/${encodeURIComponent(active.id)}`)} />
            {hero.length > 1 && <GlassIconButton focusKey="hero:rotate" icon={rotating ? 'pause' : 'play'} label={rotating ? 'Pause featured rotation' : 'Play featured rotation'} disabled={!rotation.ready} onEnter={toggleRotation} />}
            {hero.length > 1 && (
              <div className="home-hero-dots" aria-hidden="true">
                {hero.map((h, i) => <i key={h.id} data-on={i === shown} />)}
              </div>
            )}
          </FocusGroup>
        </div>
      </section>
      <div className="home-rows">
        {visibility.notice}
        {rows.map((r) => (
          <TileRow key={r.id} id={r.id} title={r.title} tiles={r.tiles} onRemove={r.id === 'continue_watching' ? visibility.remove : undefined} layout={r.layout} seeAll={r.browse_path} onFocusTile={setDriver} />
        ))}
      </div>
      <HomeFocusSentinel onClear={() => setDriver(null)} />
    </div>
  )
}

/** Returning focus to the hero buttons hands the billboard back to the carousel. */
function HomeFocusSentinel({ onClear }: { onClear: () => void }) {
  const ref = useRef(onClear)
  ref.current = onClear
  useEffect(() => {
    const el = document.querySelector('[data-testid="hero"]')
    if (!el) return
    const h = () => ref.current()
    el.addEventListener('focusin', h)
    return () => el.removeEventListener('focusin', h)
  }, [])
  return null
}
