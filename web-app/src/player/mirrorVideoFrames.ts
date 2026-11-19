/** Paint the current decoded frame, including stills after a pause or seek. */
export function mirrorVideoFrames(video: HTMLVideoElement, canvas: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext('2d', { alpha: false })
  if (!ctx) return () => {}

  const useFrameCallback = typeof video.requestVideoFrameCallback === 'function'
  let pending: number | null = null
  let encrypted = false
  let lastTime = -1

  function stop() {
    if (pending === null) return
    if (useFrameCallback) video.cancelVideoFrameCallback(pending)
    else cancelAnimationFrame(pending)
    pending = null
  }
  function hide() {
    stop()
    canvas.hidden = true
    lastTime = -1
  }
  const draw = () => {
    const width = video.videoWidth, height = video.videoHeight
    if (!width || !height || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || video.seeking) {
      hide()
      return false
    }
    try {
      if (canvas.width !== width) canvas.width = width
      if (canvas.height !== height) canvas.height = height
      ctx.drawImage(video, 0, 0, width, height)
      canvas.hidden = false
      lastTime = video.currentTime
      return true
    } catch {
      // Browsers may refuse to copy a protected picture. Keep the media element visible.
      hide()
      return false
    }
  }
  function tick() {
    pending = null
    if ((useFrameCallback || video.currentTime !== lastTime) && !draw()) return
    schedule()
  }
  function schedule() {
    if (pending !== null || video.paused || video.ended) return
    pending = useFrameCallback ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick)
  }
  const refresh = () => {
    // Browser-rendered cues are not part of drawImage(). Let the media element show them.
    const captions = Array.from(video.textTracks).some(t => t.mode === 'showing' && (t.kind === 'captions' || t.kind === 'subtitles'))
    if (encrypted || video.mediaKeys || captions || document.hidden) { hide(); return }
    if (video.paused || video.ended) stop()
    if (draw()) schedule()
  }
  const reset = () => { encrypted = false; hide() }
  const onEncrypted = () => { encrypted = true; hide() }
  const listeners = new AbortController()
  const options = { signal: listeners.signal }
  const updates = ['loadeddata', 'playing', 'pause', 'ended', 'seeked', 'resize']
  const resets = ['emptied', 'loadstart']
  const tracks = ['change', 'addtrack', 'removetrack']
  updates.forEach(event => video.addEventListener(event, refresh, options))
  resets.forEach(event => video.addEventListener(event, reset, options))
  tracks.forEach(event => video.textTracks.addEventListener(event, refresh, options))
  video.addEventListener('seeking', hide, options)
  video.addEventListener('error', hide, options)
  video.addEventListener('encrypted', onEncrypted, options)
  document.addEventListener('visibilitychange', refresh, options)
  refresh()
  return () => {
    hide()
    listeners.abort()
  }
}
