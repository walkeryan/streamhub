/**
 * A reserve for live MPEG-TS in the browser.
 *
 * IPTV feeds reach the server in bursts with gaps of a few seconds between them. The browser
 * starts a live stream with about five seconds in hand (the tuner starts each viewer that far
 * behind) and, left alone, resumes after every stall the moment the next scrap of video lands,
 * so the reserve never comes back and each later gap shows as buffering. This holds the picture
 * after a stall until `LIVE_CUSHION_S` seconds are in hand again, so one wait covers the next
 * few gaps instead of stalling on each of them. At real-time pace every second held adds a second
 * of reserve, so the wait after video comes back is at most `LIVE_CUSHION_S`; only a feed slower
 * than real time cannot build it, and it plays what it has `LIVE_HOLD_MAX_MS` after video returns.
 * While nothing arrives at all the player's own timeout (it reconnects) is in charge.
 *
 * The hold sets `playbackRate` to 0 rather than pausing: a pause would read as the viewer's
 * own (play button, progress reports, buffer heartbeats all follow `pause`), while a held
 * stream is still meant to be playing.
 */

export const LIVE_CUSHION_S = 4
export const LIVE_HOLD_MAX_MS = 8_000
const CHECK_MS = 250

export interface CushionVideo {
  currentTime: number
  paused: boolean
  playbackRate: number
  buffered: { length: number; start(i: number): number; end(i: number): number }
  addEventListener(type: string, fn: () => void): void
  removeEventListener(type: string, fn: () => void): void
}

/** Seconds of decodable video ahead of the playhead (0 when the playhead sits in a gap). */
export function bufferedAhead(video: Pick<CushionVideo, 'currentTime' | 'buffered'>): number {
  const t = video.currentTime
  const b = video.buffered
  for (let i = 0; i < b.length; i++) {
    const start = b.start(i)
    const end = b.end(i)
    // A small gap before the first range is normal at the start of a live stream: the browser steps over it.
    if (end > t && start <= t + 0.5) return end - Math.max(t, start)
  }
  return 0
}

export interface LiveCushion {
  holding: () => boolean
  stop: () => void
}

export function liveCushion(
  video: CushionVideo,
  onHold: (holding: boolean) => void,
  clock: { now: () => number; every: (fn: () => void, ms: number) => () => void } = {
    now: () => performance.now(),
    every: (fn, ms) => { const id = window.setInterval(fn, ms); return () => window.clearInterval(id) },
  },
): LiveCushion {
  let holding = false
  let since = 0
  let rate = 1
  let setting = false
  const setRate = (value: number) => { setting = true; video.playbackRate = value; setting = false }
  const release = () => {
    if (!holding) return
    holding = false
    // Only undo our own 0: a rate the viewer chose meanwhile stands.
    if (video.playbackRate === 0) setRate(rate)
    onHold(false)
  }
  const hold = () => {
    if (holding || video.paused) return
    holding = true
    since = clock.now()
    rate = video.playbackRate || 1
    setRate(0)
    onHold(true)
  }
  const check = () => {
    if (!holding) return
    const ahead = bufferedAhead(video)
    if (ahead < 0.1) { since = clock.now(); return } // nothing back yet: the limit counts from when video returns
    if (ahead >= LIVE_CUSHION_S || clock.now() - since >= LIVE_HOLD_MAX_MS) release()
  }
  // Someone else set the rate (the viewer's speed control): their choice ends the hold.
  const onRate = () => { if (holding && !setting && video.playbackRate !== 0) { holding = false; onHold(false) } }
  const onWaiting = () => { if (bufferedAhead(video) < LIVE_CUSHION_S) hold() }
  video.addEventListener('waiting', onWaiting)
  video.addEventListener('ratechange', onRate)
  const stopTimer = clock.every(check, CHECK_MS)
  return {
    holding: () => holding,
    stop: () => {
      stopTimer()
      video.removeEventListener('waiting', onWaiting)
      video.removeEventListener('ratechange', onRate)
      if (holding && video.playbackRate === 0) setRate(rate)
      holding = false
    },
  }
}
