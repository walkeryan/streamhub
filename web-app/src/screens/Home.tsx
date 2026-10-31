import { useEffect, useMemo, useRef, useState } from 'react'
import { useHome } from '../api/hooks'
import type { Tile } from '../api/types'
import { Button, Crossfade, Empty, ErrorState, Meta, Skeleton, SourceChips } from '../components/ui'
import { TileRow } from '../components/Tile'
import { FocusGroup } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useUi } from '../store/ui'
import { useAdaptiveAccent } from '../lib/theme'
import { cn } from '../lib/util'

export const isPlayable = (t: Tile) => t.kind === 'channel' || t.sources.some((s) => s.playable_in_app)

export function Home() {
  const { data, isLoading, error, refetch } = useHome()
  const prefs = useUi((s) => s.homePrefs)
  const go = useGo()
  const [idx, setIdx] = useState(0)
  const [driver, setDriver] = useState<Tile | null>(null)
  const [heroFocused, setHeroFocused] = useState(false)

  const hero = data?.hero ?? []
  const rows = useMemo(() => {
    const list = (data?.rows ?? []).filter((r) => !prefs.hidden.includes(r.id) && r.tiles.length > 0)
    const pos = (id: string) => { const i = prefs.order.indexOf(id); return i < 0 ? 1000 : i }
    return [...list].sort((a, b) => pos(a.id) - pos(b.id))
  }, [data, prefs])

  const active = driver ?? hero[idx % Math.max(1, hero.length)] ?? rows[0]?.tiles[0] ?? null
  useAdaptiveAccent(active?.backdrop ?? active?.poster)

  // Auto-advance every 8 s; paused while a hero button is focused or a row tile is driving the hero.
  useEffect(() => {
    if (hero.length < 2 || heroFocused || driver) return
    const id = window.setInterval(() => { if (!document.hidden) setIdx((i) => (i + 1) % hero.length) }, 8000)
    return () => window.clearInterval(id)
  }, [hero.length, heroFocused, driver])

  useRestoreFocus(!!data, 'hero:play')

  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  if (isLoading) return (
    <div data-testid="home-loading">
      <Skeleton style={{ height: '50dvh', borderRadius: 0 }} />
      <div className="page-pad flex gap-4">{Array.from({ length: 7 }, (_, i) => <Skeleton key={i} style={{ width: 'var(--tile-poster-w)', height: 'var(--tile-poster-h)' }} />)}</div>
    </div>
  )
  if (!active) return (
    <div className="page-pad">
      <Empty title="Nothing to watch yet" hint="Connect Plex, Jellyfin, an IPTV provider or your streaming services and your library shows up here.">
        <Button focusKey="hero:play" primary icon="plus" onEnter={() => go('/sources')}>Add a source</Button>
      </Empty>
    </div>
  )

  const playable = isPlayable(active)
  return (
    <div data-testid="home">
      <section className="hero" aria-label="Featured" data-testid="hero">
        <Crossfade src={active.backdrop ?? active.poster} />
        <div className="hero-body">
          <h1 className="display" data-testid="hero-title">{active.logo ? <img src={active.logo} alt={active.title} style={{ maxHeight: '5rem' }} /> : active.title}</h1>
          <Meta items={[active.year, active.subtitle, active.rating ? `★ ${active.rating.toFixed(1)}` : null]} />
          <div className="flex gap-2 flex-wrap"><SourceChips sources={active.sources} max={3} /></div>
          <FocusGroup focusKey="hero" className="flex gap-3 flex-wrap items-center" onFocusChange={setHeroFocused} remember={false} preferred="hero:play">
            <Button focusKey="hero:play" primary icon="play" testId="hero-play"
              onEnter={() => go(playable ? `/play/${encodeURIComponent(active.id)}` : `/title/${encodeURIComponent(active.id)}`)}>
              {playable ? 'Play' : 'Open in app'}
            </Button>
            <Button focusKey="hero:details" icon="info" testId="hero-details" onEnter={() => go(`/title/${encodeURIComponent(active.id)}`)}>Details</Button>
            {hero.length > 1 && (
              <div className="flex gap-1.5 ml-2" aria-hidden="true">
                {hero.map((h, i) => (
                  <i key={h.id} style={{ width: i === idx % hero.length ? 22 : 8, height: 8, borderRadius: 8, transition: 'width 180ms ease-out',
                    background: i === idx % hero.length ? 'var(--t-accent)' : 'color-mix(in srgb, var(--t-text) 35%, transparent)' }} />
                ))}
              </div>
            )}
          </FocusGroup>
        </div>
      </section>
      <div className={cn('pb-16')} style={{ marginTop: '-0.5rem', position: 'relative', zIndex: 3 }}>
        {rows.map((r) => (
          <TileRow key={r.id} id={r.id} title={r.title} tiles={r.tiles} layout={r.layout} onFocusTile={setDriver} />
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
