import { memo } from 'react'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { Icon } from './Icon'
import { FeatureCard } from './FeatureCard'
import { ProgressBar, SourceChips } from './ui'
import { Initials, SafeImg } from './SafeImg'
import { cn, fmtTime, hueOf, progressOf, useNow } from '../lib/util'
import { useGo } from '../nav/back'
import type { Tile as TileT, Channel } from '../api/types'

export type TileLayout = 'poster' | 'landscape' | 'channel'

export function openPath(t: Pick<TileT, 'id' | 'kind'>): string {
  return t.kind === 'channel' ? `/play/${encodeURIComponent(t.id)}` : `/title/${encodeURIComponent(t.id)}`
}

export function useOpenTile() {
  const go = useGo()
  return (t: Pick<TileT, 'id' | 'kind'>) => go(openPath(t))
}

function Fallback({ title }: { title: string }) {
  const h = hueOf(title)
  return (
    <div className="absolute inset-0 grid place-items-center p-3 text-center font-semibold"
      style={{ background: `linear-gradient(160deg, hsl(${h} 40% 28%), hsl(${(h + 50) % 360} 45% 16%))`, color: '#fff' }}>
      <span className="clamp2">{title}</span>
    </div>
  )
}

export const MediaTile = memo(function MediaTile(p: {
  tile: TileT; scope: string; layout?: TileLayout; onFocusTile?: (t: TileT) => void; onOpen?: (t: TileT) => void; titleAlways?: boolean
}) {
  const open = useOpenTile()
  const layout = p.layout ?? 'poster'
  const art = layout === 'landscape' ? (p.tile.backdrop ?? p.tile.poster) : (p.tile.poster ?? p.tile.backdrop)
  const noArt = !art
  return (
    <Focusable
      focusKey={`${p.scope}::${p.tile.id}`}
      className={cn('tile', layout === 'landscape' ? 'tile-landscape' : 'tile-poster')}
      label={p.tile.title}
      role="link"
      data-testid="tile"
      scroll
      onEnter={() => (p.onOpen ?? open)(p.tile)}
      onFocus={() => p.onFocusTile?.(p.tile)}
    >
      {({ focused }) => (
        <div data-focused={focused} data-title={noArt || p.titleAlways ? 'always' : undefined} className="tile" style={{ position: 'absolute', inset: 0, borderRadius: 'inherit' }}>
          <SafeImg className="art" src={art} alt="" loading="lazy" decoding="async" draggable={false} fallback={<Fallback title={p.tile.title} />} />
          <div className="cap"><span className="clamp2">{p.tile.title}{p.tile.year ? ` (${p.tile.year})` : ''}</span></div>
          <div className="tile-badges"><SourceChips sources={p.tile.sources} max={1} /></div>
          {p.tile.is_favorite && <span style={{ position: 'absolute', top: 6, left: 6, color: 'var(--t-accent)' }}><Icon name="heart" size={16} filled /></span>}
          {typeof p.tile.progress_pct === 'number' && p.tile.progress_pct > 0 && (
            <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0 }}><ProgressBar pct={p.tile.progress_pct} /></div>
          )}
        </div>
      )}
    </Focusable>
  )
})

/** Live tile: 220x96 logo on a coloured block, number, and now-playing progress. Accepts a Tile (kind=channel) or a Channel. */
export function ChannelTile(p: { tile: TileT; scope: string; onFocusTile?: (t: TileT) => void }) {
  const open = useOpenTile()
  const now = useNow()
  const t = p.tile
  const h = hueOf(t.title)
  const pct = t.now_playing ? progressOf(t.now_playing.start, t.now_playing.stop, now) : null
  return (
    <Focusable
      focusKey={`${p.scope}::${t.id}`}
      className="tile tile-channel"
      label={`${t.title}${t.now_playing ? `, now ${t.now_playing.title}` : ''}`}
      role="link"
      data-testid="tile"
      onEnter={() => open(t)}
      onFocus={() => p.onFocusTile?.(t)}
    >
      <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(135deg, hsl(${h} 45% 30%), hsl(${(h + 45) % 360} 50% 18%))` }}>
        <SafeImg src={t.logo} alt="" loading="lazy" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', padding: 14, filter: 'blur(0)' }}
          fallback={<Initials name={t.title} style={{ fontSize: 'var(--text-title)', paddingBottom: '1.25rem' }} />} />
        <div style={{ position: 'absolute', left: 10, top: 8, fontWeight: 700, fontSize: 'var(--text-label)' }}>{t.subtitle ?? ''}</div>
        <div style={{ position: 'absolute', left: 10, right: 10, bottom: 12, fontSize: 'var(--text-label)', fontWeight: 600 }} className="truncate">
          {t.title}{t.now_playing ? ` · ${t.now_playing.title}` : ''}
        </div>
        {pct !== null && <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0 }}><ProgressBar pct={pct} /></div>}
      </div>
    </Focusable>
  )
}

export function channelAsTile(c: Channel): TileT {
  return {
    id: c.id, kind: 'channel', title: c.name, subtitle: String(c.number), logo: c.logo ?? null, is_favorite: !!c.is_favorite,
    sources: [{ source_id: c.source_id, source_type: 'xtream', name: 'IPTV', playable_in_app: true, kind: 'live' }],
    now_playing: c.now ?? undefined,
  }
}

function FeatureTile({ tile, scope, onFocusTile, now }: { tile: TileT; scope: string; onFocusTile?: (tile: TileT) => void; now: number }) {
  const open = useOpenTile()
  const programme = tile.now_playing
  const airing = programme && Date.parse(programme.start) <= now && Date.parse(programme.stop) > now
  const channel = tile.kind === 'channel'
  const eyebrow = channel ? `${tile.title}${programme ? ` · ${fmtTime(programme.start)}–${fmtTime(programme.stop)}` : ''}` : [tile.year, tile.sources.map((s) => s.name).slice(0, 2).join(' · ')].filter(Boolean).join(' · ')
  return <FeatureCard focusKey={`${scope}::${tile.id}`} label={tile.title} title={programme?.title ?? tile.title} eyebrow={eyebrow}
    art={channel ? tile.backdrop : tile.backdrop ?? tile.poster} logo={tile.logo} fallbackName={tile.title}
    badge={airing ? 'On now' : channel ? 'Channel' : undefined}
    progress={airing ? progressOf(programme.start, programme.stop, now) : tile.progress_pct}
    onEnter={() => open(tile)} onFocus={() => onFocusTile?.(tile)} testId="tile">
    {!channel && <SourceChips sources={tile.sources} max={1} />}
  </FeatureCard>
}

export function TileRow(p: {
  id: string; title: string; tiles: TileT[]; layout?: 'poster' | 'landscape' | 'hero' | 'channel'
  onFocusTile?: (t: TileT) => void; empty?: string; presentation?: 'wide'
}) {
  const layout = p.layout ?? 'poster'
  const now = useNow(30_000) // one clock per row, not per media card
  if (!p.tiles.length) return null
  return (
    <section aria-label={p.title} data-testid="row" data-row={p.id} style={{ marginBottom: '0.5rem' }}>
      <h2 className="row-title">{p.title}</h2>
      <FocusGroup focusKey={`row:${p.id}`} className={cn('row-scroll', p.presentation === 'wide' && 'feature-row')} remember>
        {p.tiles.map((t) =>
          p.presentation === 'wide' ? <FeatureTile key={t.id} tile={t} scope={p.id} onFocusTile={p.onFocusTile} now={now} /> : layout === 'channel' || t.kind === 'channel' ? (
            <ChannelTile key={t.id} tile={t} scope={p.id} onFocusTile={p.onFocusTile} />
          ) : (
            <MediaTile key={t.id} tile={t} scope={p.id} layout={layout === 'landscape' ? 'landscape' : 'poster'} onFocusTile={p.onFocusTile} />
          ),
        )}
      </FocusGroup>
    </section>
  )
}
