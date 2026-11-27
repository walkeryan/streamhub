import { useEffect, useImperativeHandle, useRef, useState, type Ref, type RefObject } from 'react'
import { Button } from '../components/ui'
import { fmtDuration } from '../lib/util'
import { Scrubber } from './Scrubber'
import type { BufferWindow } from './bufferSession'

export type LiveSeek = { available: boolean; step: (seconds: number) => void; start: () => void; goLive: () => void }
type Bounds = { start: number; end: number; position: number }
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Only a contiguous range that contains the playhead is safe to seek in locally. */
function playableRange(v: HTMLMediaElement, first: number, furthest: number): Bounds | null {
  for (let i = 0; i < v.seekable.length; i++) {
    const start = Math.max(first, v.seekable.start(i))
    const limit = v.seekable.end(i)
    if (limit <= start || v.currentTime < start - .25 || v.currentTime > limit + .25) continue
    // Downloaded bytes alone do not guarantee seek support. Some direct servers expose
    // a complete buffered file with an empty (or zero-length) seekable range.
    let end = Math.min(furthest, limit)
    for (let j = 0; j < v.buffered.length; j++) {
      if (v.buffered.start(j) <= v.currentTime + .25 && v.buffered.end(j) >= v.currentTime - .25) {
        end = Math.min(limit, v.buffered.end(j))
        break
      }
    }
    if (end > start) return { start, end, position: clamp(v.currentTime, start, end) }
  }
  return null
}

export function LocalBufferControls({ window: lease, media, programmeTime, playbackAt, seekServer, resume, control }: {
  window: BufferWindow | null; media: RefObject<HTMLMediaElement | null>; programmeTime: () => number | undefined
  playbackAt: string | null; seekServer: (at: string | null) => unknown; resume: () => void; control: Ref<LiveSeek>
}) {
  const [local, setLocal] = useState<Bounds | null>(null)
  const [utc, setUtc] = useState<number | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const clock = useRef(programmeTime)
  clock.current = programmeTime
  useEffect(() => {
    const video = media.current
    if (!video) return
    let first: number | null = null
    let furthest = 0
    let playbackStart: number | null = null
    const reset = () => { first = null; furthest = 0; playbackStart = null; setLocal(null); setUtc(null); setTarget(null) }
    const sample = () => {
      if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return
      first ??= video.buffered.length ? Math.min(video.currentTime, video.buffered.start(0)) : video.currentTime
      furthest = Math.max(furthest, video.currentTime)
      playbackStart ??= video.currentTime
      setLocal(playableRange(video, first, furthest))
      const stamp = clock.current()
      setUtc(stamp != null && Number.isFinite(stamp) ? stamp / 1000 : playbackAt
        ? Date.parse(playbackAt) / 1000 + video.currentTime - playbackStart : null)
    }
    reset(); sample()
    const events = ['loadeddata', 'timeupdate', 'progress', 'seeked', 'durationchange']
    for (const name of events) video.addEventListener(name, sample)
    video.addEventListener('emptied', reset)
    return () => { for (const name of events) video.removeEventListener(name, sample); video.removeEventListener('emptied', reset) }
  }, [media, playbackAt])
  const start = lease?.start ? Date.parse(lease.start) / 1000 : NaN
  const end = lease?.end ? Date.parse(lease.end) / 1000 : NaN
  const server = end > start
  const bounds = server ? { start, end, position: clamp(utc ?? (playbackAt ? Date.parse(playbackAt) / 1000 : end), start, end) } : local
  const duration = bounds ? bounds.end - bounds.start : 0
  const elapsed = bounds ? bounds.position - bounds.start : 0
  const goLive = () => {
    setTarget(null)
    if (server || playbackAt) { void seekServer(null); return }
    if (media.current && local) { media.current.currentTime = Math.max(local.start, local.end - .1); resume() }
  }
  const commit = (seconds: number) => {
    if (!bounds) return
    setTarget(null)
    const to = clamp(seconds, 0, duration)
    if (to >= duration - .25) { goLive(); return }
    if (server) { void seekServer(new Date((bounds.start + to) * 1000).toISOString()); return }
    if (media.current && local) media.current.currentTime = clamp(bounds.start + to, local.start, local.end)
  }
  useImperativeHandle(control, () => ({ available: !!bounds, step: s => commit(elapsed + s), start: () => commit(0), goLive }))
  const label = (s: number) => duration - s <= 1 ? 'Live' : fmtDuration(Math.ceil(duration - s)) + ' behind live'
  const preview = bounds && target !== null ? clamp(target - bounds.start, 0, duration) : null
  return <div data-testid="local-buffer-controls" className="watched-rewind">
    <Scrubber label="Rewind watched channel" format={label} position={elapsed} duration={duration} loaded={bounds ? [[0, duration]] : []}
      target={preview} hint={null} busy={false} disabled={!bounds} onSeek={commit}
      onStep={dir => { if (bounds) setTarget(t => clamp((t ?? bounds.position) + dir * 10, bounds.start, bounds.end)) }}
      onEnter={() => { if (preview !== null) commit(preview) }} />
    <div className="ptimes"><span>{bounds ? fmtDuration(Math.floor(duration)) + ' available' : 'Building rewind window…'}</span>
      {bounds && <><span>{label(elapsed)}</span><Button focusKey="buffer:live" ghost onEnter={goLive}>Go Live</Button></>}
    </div>
  </div>
}
