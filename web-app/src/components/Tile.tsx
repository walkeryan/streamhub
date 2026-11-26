import { memo } from 'react'
import { Focusable } from '../nav/Focusable'
import { Icon } from './Icon'
import { FeatureCard } from './FeatureCard'
import { CardActions } from './CardActions'
import { ProgressBar, SourceChips } from './ui'
import { Initials, SafeImg } from './SafeImg'
import { cn, fmtTime, hueOf, progressOf, useNow } from '../lib/util'
import { AvailableTick, OverflowMenu, HRow, SectionHeader, StillCard } from '../ds'
import { useGo } from '../nav/back'
import { tileRequest } from '../lib/requests'
import { RequestTileStatus } from './RequestStatus'
import type { Tile as TileT, Channel, SourceBadge } from '../api/types'

export type TileLayout = 'poster' | 'landscape' | 'channel'

export function openPath(t: Pick<TileT, 'id' | 'kind'>): string {
  return t.kind === 'channel' ? `/play/${encodeURIComponent(t.id)}` : `/title/${encodeURIComponent(t.id)}`
}

export function useOpenTile() {
  const go = useGo()
  return (t: Pick<TileT, 'id' | 'kind'>) => go(openPath(t))
}

/**
 * Where a title can be watched, when that is worth a badge. A copy in the household's own library
 * is: "In your library". So is a choice between sources, and a title that only opens in another
 * app. One ordinary source is what every tile of a one-source catalog has, and says nothing.
 */
export function TileBadge({ sources, max = 1 }: { sources: SourceBadge[]; max?: number }) {
  if (sources.some((s) => s.kind === 'library' || s.source_type === 'plex' || s.source_type === 'jellyfin')) return <span className="badge badge-play" data-testid="tile-badge">In your library</span>
  return namesSource(sources) ? <SourceChips sources={sources} max={max} /> : null
}
/** Is there anything to say about where this plays: the household's library, a choice of sources, or another app? */
export const namesSource = (sources: SourceBadge[]): boolean =>
  sources.some((s) => s.kind === 'library' || s.source_type === 'plex' || s.source_type === 'jellyfin' || !s.playable_in_app) || new Set(sources.map((s) => s.source_id)).size > 1

function Fallback({ title }: { title: string }) {
  const h = hueOf(title)
  return <div className="tile-art-fallback absolute inset-0" aria-hidden="true"
    style={{ background: `linear-gradient(160deg, hsl(${h} 40% 28%), hsl(${(h + 50) % 360} 45% 16%))` }} />
}

export const MediaTile = memo(function MediaTile(p: {
  tile: TileT; scope: string; layout?: TileLayout; onFocusTile?: (t: TileT) => void; onOpen?: (t: TileT) => void; titleAlways?: boolean
  /**
   * Search mixes what can be watched here with what is only known about. There a tile says one thing:
   * a green tick when it plays here, nothing when it does not (a request in progress still shows itself).
   */
  mark?: 'availability'
  captionBelow?: boolean
}) {
  const open = useOpenTile()
  const layout = p.layout ?? 'poster'
  const art = layout === 'landscape' ? (p.tile.backdrop ?? p.tile.poster) : (p.tile.poster ?? p.tile.backdrop)
  const noArt = !art
  const request = tileRequest(p.tile)
  return (
    <Focusable
      focusKey={`${p.scope}::${p.tile.id}`}
      className={p.captionBelow ? 'ds-captioned tp-like' : cn('tile', layout === 'landscape' ? 'tile-landscape' : 'tile-poster')}
      label={p.captionBelow && p.tile.year ? `${p.tile.title} (${p.tile.year})` : p.tile.title}
      role="link"
      data-testid="tile"
      scroll
      onEnter={() => (p.onOpen ?? open)(p.tile)}
      onFocus={() => p.onFocusTile?.(p.tile)}
    >
      {({ focused }) => (<>
        <div data-focused={focused} data-title={noArt || p.titleAlways ? 'always' : undefined} className={cn('tile', p.captionBelow && 'tile-poster')} style={p.captionBelow ? undefined : { position: 'absolute', inset: 0, borderRadius: 'inherit' }}>
          <SafeImg className="art" src={art} alt="" loading="lazy" decoding="async" draggable={false} reveal fallback={<Fallback title={p.tile.title} />} />
          {!p.captionBelow && <div className="cap"><span className="clamp2">{p.tile.title}{p.tile.year ? ` (${p.tile.year})` : ''}</span></div>}
          {/* A request on its way says where it is, in place of where the title plays. */}
          {request ? <RequestTileStatus progress={request} of={p.tile.title} /> : p.mark === 'availability' ? (
            p.tile.sources.some((s) => s.playable_in_app) && <AvailableTick />
          ) : <div className="tile-badges">{p.tile.sources.length ? <TileBadge sources={p.tile.sources} /> : <span className="badge">Not available here</span>}</div>}
          {p.tile.is_favorite && <span className="tile-fav" role="img" aria-label="Favorite"><Icon name="heart" filled /></span>}
          {typeof p.tile.progress_pct === 'number' && p.tile.progress_pct > 0 && <ProgressBar label={`Viewing progress · ${p.tile.title}`} pct={p.tile.progress_pct} className="tile-progress" />}
        </div>
        {p.captionBelow && <p className="ds-cap-below"><span className="clamp2">{p.tile.title}</span>{p.tile.year && <small>{p.tile.year}</small>}</p>}
      </>)}
    </Focusable>
  )
})

/** Live tile, 220x96 on a coloured block: the logo in its own box on the left, the channel and what is on
 *  beside it, now-playing progress along the bottom. Logo and words never share space, whatever the logo's shape. */
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
      <div className="channel-card" style={{ background: `linear-gradient(135deg, hsl(${h} 45% 30%), hsl(${(h + 45) % 360} 50% 18%))` }}>
        <div className="channel-logo"><SafeImg src={t.logo} alt="" loading="lazy" fallback={<Initials name={t.title} style={{ background: 'transparent' }} />} /></div>
        <div className="channel-copy">
          <span className="channel-name truncate">{t.subtitle ? `${t.subtitle} · ` : ''}{t.title}</span>
          <span className="channel-now clamp2">{t.now_playing?.title ?? 'No programme data'}</span>
        </div>
        {pct !== null && <ProgressBar label={`Programme progress · ${t.now_playing?.title ?? t.title}`} pct={pct} />}
      </div>
    </Focusable>
  )
}

/**
 * A title in progress as a landscape still with its name and where it is under it (Continue Watching):
 * the backdrop (else the poster), the progress along its foot, a request's status or where it plays in
 * its corner.
 */
export function LandscapeTile(p: { tile: TileT; scope: string; onFocusTile?: (t: TileT) => void }) {
  const open = useOpenTile()
  const t = p.tile
  const request = tileRequest(t)
  const pct = t.progress_pct ?? 0
  return (
    <StillCard focusKey={`${p.scope}::${t.id}`} testId="tile" label={t.title} onEnter={() => open(t)} onFocus={() => p.onFocusTile?.(t)}
      image={t.backdrop ?? t.poster} fallbackName={t.title} title={t.title} meta={t.subtitle ?? t.year ?? undefined}
      progressPct={pct} watched={pct >= 100}
      badge={request ? undefined : namesSource(t.sources) ? <TileBadge sources={t.sources} /> : undefined}
      corner={request ? <RequestTileStatus progress={request} of={t.title} /> : undefined} />
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
    {!channel && <TileBadge sources={tile.sources} />}
  </FeatureCard>
}

/** Last tile of a row: opens the row's full listing. Shaped like its neighbours so the row keeps one rhythm. */
function SeeAllTile({ scope, title, to, count, layout }: { scope: string; title: string; to: string; count?: number; layout: TileLayout }) {
  const go = useGo()
  return (
    <Focusable focusKey={`${scope}::see-all`} className={cn('tile tile-more', `tile-${layout}`)} role="link" label={`See all ${title}`}
      data-testid="see-all" scroll onEnter={() => go(to)}>
      <Icon name="chevR" size={28} />
      <span>See all</span>
      {count !== undefined && <span className="label">{count.toLocaleString()} titles</span>}
    </Focusable>
  )
}

export function TileRow(p: {
  id: string; title: string; tiles: TileT[]; layout?: 'poster' | 'landscape' | 'hero' | 'channel'
  onRemove?: (t: TileT) => void; onFocusTile?: (t: TileT) => void; empty?: string; presentation?: 'wide'
  /** route of the full listing; adds a "See all" tile after the last one */
  seeAll?: string | null; count?: number
}) {
  const layout = p.layout ?? 'poster'
  const now = useNow(30_000) // one clock per row, not per media card
  if (!p.tiles.length) return null
  return (
    <section aria-label={p.title} data-testid="row" data-row={p.id} data-layout={layout} className="tile-row ds-section">
      <SectionHeader title={p.title} />
      <HRow focusKey={`row:${p.id}`} className={cn(p.presentation === 'wide' && 'feature-row')}>
        {p.tiles.map((t) =>
          p.onRemove ? <CardActions className="continue-tile" key={t.id} cardKey={`${p.id}::${t.id}`} menuKey={`continue:more:${t.id}`}>
            <LandscapeTile tile={t} scope={p.id} onFocusTile={p.onFocusTile} />
            <div className="continue-menu"><OverflowMenu focusKey={`continue:more:${t.id}`} menuKey={`continue:menu:${t.id}`} title={t.title}
              label={`More actions for ${t.title}`} items={[{ key: 'remove', icon: 'x', label: 'Remove from Continue Watching', onEnter: () => p.onRemove?.(t) }]} /></div>
          </CardActions> : p.presentation === 'wide' ? <FeatureTile key={t.id} tile={t} scope={p.id} onFocusTile={p.onFocusTile} now={now} /> : layout === 'channel' || t.kind === 'channel' ? (
            <ChannelTile key={t.id} tile={t} scope={p.id} onFocusTile={p.onFocusTile} />
          ) : layout === 'landscape' ? (
            // artwork cut for a wide tile rarely carries the title, so it is always captioned (under it)
            <LandscapeTile key={t.id} tile={t} scope={p.id} onFocusTile={p.onFocusTile} />
          ) : (
            <MediaTile key={t.id} tile={t} scope={p.id} layout="poster" onFocusTile={p.onFocusTile} />
          ),
        )}
        {p.seeAll && <SeeAllTile scope={p.id} title={p.title} to={p.seeAll} count={p.count} layout={layout === 'landscape' || layout === 'channel' ? layout : 'poster'} />}
      </HRow>
    </section>
  )
}
