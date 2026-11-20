import { useEffect, useRef, type CSSProperties, type RefObject } from 'react'
import { paintServerFrames } from './serverCanvas'
import { mirrorVideoFrames } from './mirrorVideoFrames'

/** Uses server JPEGs when the descriptor carries them. Older servers and watch rooms use the decoded video. */
export function CanvasPicture({ videoRef, fit, audioRef }: {
  videoRef: RefObject<HTMLVideoElement | null>
  audioRef?: RefObject<HTMLAudioElement | null>
  fit: CSSProperties['objectFit']
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const video = videoRef.current
    const canvas = ref.current
    if (audioRef?.current && canvas) return paintServerFrames(audioRef.current, canvas)
    if (!video || !canvas) return
    return mirrorVideoFrames(video, canvas)
  }, [videoRef, audioRef])
  return <canvas ref={ref} className="canvas-picture" hidden aria-hidden="true" style={{ objectFit: fit }} />
}
