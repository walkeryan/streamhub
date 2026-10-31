import { useRef } from 'react'
import { usePlayback } from './usePlayback'

/** Muted inline preview for Live TV. Does not report progress. */
export function Preview({ channelId }: { channelId: string | null }) {
  const ref = useRef<HTMLVideoElement>(null)
  const req = channelId ? { id: channelId } : null
  const { state, error } = usePlayback(ref, req, { muted: true, report: false })
  return (
    <div style={{ position: 'relative', aspectRatio: '16/9', background: '#000', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }} data-testid="live-preview" data-state={state}>
      <video ref={ref} muted playsInline loop aria-label="Channel preview" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      {(state === 'resolving' || state === 'loading' || state === 'reconnecting') && channelId && (
        <div className="grid place-items-center" style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,.45)', color: '#fff' }} role="status">
          {state === 'reconnecting' ? 'Reconnecting…' : 'Tuning…'}
        </div>
      )}
      {state === 'error' && <div className="grid place-items-center p-4 text-center" style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,.7)', color: '#fff' }} role="alert">{error}</div>}
      {!channelId && <div className="grid place-items-center muted" style={{ position: 'absolute', inset: 0 }}>Select a channel to preview</div>}
    </div>
  )
}
