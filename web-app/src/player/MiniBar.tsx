import type { ReactNode } from 'react'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { fmtDuration } from '../lib/util'
import { usePhone } from '../lib/layout'
import { PlayerIcon, type PlayerIconName } from './PlayerIcon'
import { SafeImg } from '../components/SafeImg'
import '../styles/mini.css'

/** A round glass button of the player and the mini player (mockup: `.gb`). */
export function GlassButton(p: {
  focusKey: string; icon: PlayerIconName; label: string; onEnter: () => void; big?: boolean; disabled?: boolean
  pressed?: boolean; testId?: string; title?: string; className?: string
}) {
  return (
    <Focusable focusKey={p.focusKey} onEnter={p.onEnter} disabled={p.disabled} label={p.label} title={p.title ?? p.label}
      aria-pressed={p.pressed} data-testid={p.testId} scroll={false} className={`gbtn${p.big ? ' gbtn-big' : ''}${p.className ? ` ${p.className}` : ''}`}>
      <PlayerIcon name={p.icon} />
    </Focusable>
  )
}

export interface MiniBarProps {
  title: string
  /** "S2 · E3 · Episode 3"; null for a film or a channel's programme-less name. */
  line: string | null
  positionMs: number
  durationMs: number | null
  playing: boolean
  live: boolean
  hasPrevious: boolean
  hasNext: boolean
  /** The picture: the web player's own <video> sits over this box; the TV's bar shows the artwork. */
  image?: string | null
  thumb?: ReactNode
  onPlayPause: () => void
  onSeekBy?: (deltaMs: number) => void
  onPrevious?: () => void
  onNext?: () => void
  onExpand: () => void
  onClose: () => void
  onTogether?: () => void
  onAudio?: () => void
}

/**
 * The bar along the bottom of the app while something plays minimised (mockup 1): picture, title and
 * episode with the time, the transport in the middle, and Watch together, audio, expand and close on
 * the right. A phone gets one compact row: picture, titles, play/pause and close (the titles expand).
 */
export function MiniBar(p: MiniBarProps) {
  const phone = usePhone()
  const pct = p.durationMs && p.durationMs > 0 && !p.live ? Math.min(100, (p.positionMs / p.durationMs) * 100) : p.live ? 100 : 0
  const time = p.live ? 'Live' : `${fmtDuration(p.positionMs / 1000)}${p.durationMs ? ` / ${fmtDuration(p.durationMs / 1000)}` : ''}`
  const seek = !p.live && p.onSeekBy
  return (
    <FocusGroup focusKey="MINI" className="mini-bar" data-testid="mini-player" label="Mini player" data={{ live: p.live }}>
      <div className="mini-progress" aria-hidden="true"><i style={{ width: `${pct}%` }} /></div>
      <div className="mini-thumb" onClick={p.onExpand} data-testid="mini-thumb">
        {p.thumb ?? (p.image ? <SafeImg src={p.image} alt="" fallback={null} /> : null)}
      </div>
      <Focusable focusKey="mini:titles" className="mini-titles" label={`${p.title}. Expand the player.`} onEnter={p.onExpand} scroll={false} data-testid="mini-titles">
        <b className="truncate" data-testid="mini-title">{p.title}</b>
        <small className="truncate">{p.line ? <>{p.line}<span className="mini-dot">·</span></> : null}<span data-testid="mini-time">{time}</span></small>
      </Focusable>
      {phone ? (
        <div className="mini-controls">
          <GlassButton focusKey="mini:play" big icon={p.playing ? 'pause' : 'play'} label={p.playing ? 'Pause' : 'Play'} onEnter={p.onPlayPause} testId="mini-play" />
          <GlassButton focusKey="mini:close" icon="x" label="Close the player" onEnter={p.onClose} testId="mini-close" />
        </div>
      ) : (
        <>
          <div className="mini-transport">
            {(p.hasPrevious || p.hasNext) && <GlassButton focusKey="mini:prev" icon="prev" label="Previous episode" disabled={!p.hasPrevious} onEnter={() => p.onPrevious?.()} testId="mini-prev" />}
            {seek && <GlassButton focusKey="mini:back10" icon="back10" label="Back 10 seconds" onEnter={() => p.onSeekBy?.(-10_000)} testId="mini-back10" />}
            <GlassButton focusKey="mini:play" big icon={p.playing ? 'pause' : 'play'} label={p.playing ? 'Pause' : 'Play'} onEnter={p.onPlayPause} testId="mini-play" />
            {seek && <GlassButton focusKey="mini:fwd30" icon="fwd30" label="Forward 30 seconds" onEnter={() => p.onSeekBy?.(30_000)} testId="mini-fwd30" />}
            {(p.hasPrevious || p.hasNext) && <GlassButton focusKey="mini:next" icon="next" label="Next episode" disabled={!p.hasNext} onEnter={() => p.onNext?.()} testId="mini-next" />}
          </div>
          <div className="mini-end">
            {p.onTogether && <GlassButton focusKey="mini:together" icon="people" label="Watch together" onEnter={p.onTogether} testId="mini-together" />}
            {p.onAudio && <GlassButton focusKey="mini:audio" icon="audio" label="Audio and subtitles" onEnter={p.onAudio} testId="mini-audio" />}
            <GlassButton focusKey="mini:expand" icon="expand" label="Expand the player" onEnter={p.onExpand} testId="mini-expand" />
            <GlassButton focusKey="mini:close" icon="x" label="Close the player" onEnter={p.onClose} testId="mini-close" />
          </div>
        </>
      )}
    </FocusGroup>
  )
}
