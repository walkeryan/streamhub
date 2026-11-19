import { useEffect, useRef, type CSSProperties, type RefObject } from 'react'
import { mirrorVideoFrames } from './mirrorVideoFrames'

/** Mirrors the decoded picture. The media element still owns sound, seeking, captions and PiP. */
export function CanvasPicture({ videoRef, fit }: {
  videoRef: RefObject<HTMLVideoElement | null>
  fit: CSSProperties['objectFit']
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const video = videoRef.current
    const canvas = ref.current
    if (!video || !canvas) return
    return mirrorVideoFrames(video, canvas)
  }, [videoRef])
  return <canvas ref={ref} className="canvas-picture" hidden aria-hidden="true" style={{ objectFit: fit }} />
}
