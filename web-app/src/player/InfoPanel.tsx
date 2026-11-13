import { useEffect, useState, type RefObject } from 'react'
import { Icon } from '../components/Icon'
import { bufferedAhead, infoSections, type LiveNumbers, type MediaInfo } from './mediaInfo'

/**
 * Stream information: what the server says is playing and how it delivers it (the descriptor's
 * media_info), and what this player measures of it, read from the media element once a second.
 * It takes no remote focus: I, its button and Back close it, and playback goes on behind it.
 */
export function InfoPanel(p: {
  info: MediaInfo | null | undefined
  videoRef: RefObject<HTMLVideoElement | null>
  /** Seconds into the title at which the media element's time zero lies; null when the stream is the whole title. */
  streamStartS: number | null
  downloadKbps: () => number | null
  onClose: () => void
}) {
  const { videoRef, streamStartS, downloadKbps } = p
  const [live, setLive] = useState<LiveNumbers | null>(null)
  useEffect(() => {
    const read = () => {
      const v = videoRef.current
      if (!v) return
      const ranges = Array.from({ length: v.buffered.length }, (_, i) => [v.buffered.start(i), v.buffered.end(i)] as const)
      const quality = v.getVideoPlaybackQuality?.()
      setLive({
        bufferedAheadS: v.readyState > 0 ? bufferedAhead(ranges, v.currentTime) : null,
        droppedFrames: quality ? quality.droppedVideoFrames : null,
        totalFrames: quality ? quality.totalVideoFrames : null,
        downloadKbps: downloadKbps(),
        positionS: (streamStartS ?? 0) + v.currentTime,
        streamStartS,
        decodedWidth: v.videoWidth,
        decodedHeight: v.videoHeight,
        speed: v.playbackRate,
      })
    }
    read()
    const timer = window.setInterval(read, 1000)
    return () => window.clearInterval(timer)
    // downloadKbps reads through a ref inside the player: a new function every render, the same answer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoRef, streamStartS])
  const sections = infoSections(p.info, live)
  return (
    <aside className="pinfo" data-testid="player-info" aria-label="Stream information">
      <header>
        <h2 className="heading">Stream information</h2>
        <button type="button" className="pinfo-close" tabIndex={-1} aria-label="Close stream information" onClick={p.onClose}><Icon name="x" size={18} /></button>
      </header>
      {!p.info && <p className="label">The server did not describe this stream.</p>}
      {/* Two columns where there is width: a remote cannot scroll a panel that takes no focus, so it has to fit. */}
      <div className="pinfo-body">
        {sections.map((section) => (
          <section key={section.title} data-testid="player-info-section" data-section={section.title}>
            <h3 className="label">{section.title}</h3>
            <dl>
              {section.rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
            </dl>
          </section>
        ))}
      </div>
    </aside>
  )
}
