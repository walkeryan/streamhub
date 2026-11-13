import { useMemo, useRef } from 'react'
import { usePlayback } from './usePlayback'
import { useChannelAudio } from './channelAudio'
import { NO_POSTER } from './poster'

/**
 * Muted inline preview for Live TV. Does not report progress. It keeps a true 16:9 picture: its width
 * is what the column offers, or less when the window is short, so that the channel's name and the Watch
 * and Preview controls under it stay on screen.
 */
export function Preview({ channelId, off, onTurnOn }: {
  channelId: string | null
  /** The viewer has previews switched off: say so, rather than asking them to select a channel they already selected. */
  off?: boolean
  onTurnOn?: () => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const audio = useChannelAudio({ id: channelId ?? '' }, false)
  // A preview is muted, so which language a feed speaks cannot matter here: where full-screen playback
  // would stop to ask about audio, the preview shows the first source and stays silent.
  const first = audio.query.data?.options[0]
  const req = useMemo(
    () => !channelId ? null : audio.request ?? (first ? { id: channelId, variantId: first.variant_id, allowUnverifiedAudio: true } : null),
    [channelId, audio.request, first],
  )
  const unavailable = !!channelId && audio.blocked && !audio.query.isPending && !first
  const { state, error } = usePlayback(ref, req, { muted: true, report: false })
  return (
    <div style={{ position: 'relative', aspectRatio: '16/9', width: 'min(100%, calc((100dvh - var(--bar-h, 0px) - 19rem) * 16 / 9))', minWidth: 'min(100%, 20rem)', background: '#000', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }} data-testid="live-preview" data-state={state}>
      <video ref={ref} muted playsInline poster={NO_POSTER} loop aria-label="Channel preview" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      {(state === 'resolving' || state === 'loading' || state === 'buffering' || state === 'reconnecting') && channelId && (
        <div className="grid place-items-center" style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,.45)', color: '#fff' }} role="status">
          {state === 'reconnecting' ? 'Reconnecting…' : state === 'buffering' ? 'Buffering…' : 'Tuning…'}
        </div>
      )}
      {state === 'error' && <div className="grid place-items-center p-4 text-center" style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,.7)', color: '#fff' }} role="alert">{error}</div>}
      {unavailable && <div className="grid place-items-center p-4 text-center" style={{ position: 'absolute', inset: 0, color: '#fff' }}>No source is available to preview this channel.</div>}
      {!channelId && off && (
        <button type="button" tabIndex={-1} className="grid place-items-center p-4 text-center muted" onClick={onTurnOn} data-testid="live-preview-off"
          style={{ position: 'absolute', inset: 0, width: '100%', background: 'none', border: 0, color: 'inherit', font: 'inherit', cursor: 'pointer' }}>
          <span>Preview is off<br /><span className="label">Turn it on to watch the selected channel here. A preview uses one of the source's connections.</span></span>
        </button>
      )}
      {!channelId && !off && <div className="grid place-items-center muted" style={{ position: 'absolute', inset: 0 }}>Select a channel to preview</div>}
    </div>
  )
}
