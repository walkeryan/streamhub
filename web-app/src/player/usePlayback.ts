import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { api, reportProgress, unwrap } from '../api/client'
import type { PlaybackDescriptor } from '../api/types'
import { attachSource, type PlayerHandle } from './engine'

export type PlayState = 'resolving' | 'loading' | 'playing' | 'paused' | 'reconnecting' | 'error' | 'handoff'

export interface PlaybackReq { id: string; sourceId?: string | null; catchupProgrammeId?: string | null }

export async function resolve(req: PlaybackReq): Promise<PlaybackDescriptor> {
  return unwrap<PlaybackDescriptor>(
    api.POST('/playback/resolve', {
      body: { id: req.id, source_id: req.sourceId ?? null, client: 'web', prefer_hls: /iPad|iPhone|iPod/.test(navigator.userAgent) || /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent) || !('MediaSource' in window), catchup_programme_id: req.catchupProgrammeId ?? null },
    }),
  )
}

const MAX_RETRIES = 6

/**
 * Resolves + attaches a stream to <video>. Reconnects with exponential backoff on fatal errors by re-resolving
 * (fresh proxy token). Reports progress every 10 s, on pause/ended and on page unload.
 */
export function usePlayback(videoRef: RefObject<HTMLVideoElement | null>, req: PlaybackReq | null, opts: { muted?: boolean; report?: boolean } = {}) {
  const [desc, setDesc] = useState<PlaybackDescriptor | null>(null)
  const [state, setState] = useState<PlayState>('resolving')
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [tracksVersion, setTracksVersion] = useState(0)
  const handle = useRef<PlayerHandle | null>(null)
  const descRef = useRef<PlaybackDescriptor | null>(null)
  const retry = useRef(0)
  const retryTimer = useRef<number | null>(null)
  const clearRetry = useCallback(() => {
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [])
  const key = req ? `${req.id}|${req.sourceId ?? ''}|${req.catchupProgrammeId ?? ''}` : ''

  const reconnect = useCallback((_reason: string) => {
    if (retryTimer.current !== null) return
    if (retry.current >= MAX_RETRIES) {
      setState('error')
      setError('The stream could not start or lost its connection. Try again or choose another channel.')
      return
    }
    const wait = Math.min(15_000, 500 * 2 ** retry.current++)
    setState('reconnecting')
    retryTimer.current = window.setTimeout(() => { retryTimer.current = null; setAttempt((a) => a + 1) }, wait)
  }, [])

  // resolve + attach
  useEffect(() => {
    if (!req) return
    let cancelled = false
    const controller = new AbortController()
    let owned: PlayerHandle | null = null
    const video = videoRef.current
    setState((s) => (s === 'reconnecting' ? s : 'resolving'))
    setError(null)
    ;(async () => {
      try {
        const d = await resolve(req)
        if (cancelled) return
        setDesc(d)
        descRef.current = d
        if (d.mode === 'handoff') { setState('handoff'); return }
        if (!video) return
        setState('loading')
        handle.current?.destroy()
        owned = await attachSource(video, d, {
          signal: controller.signal,
          onFatal: (r) => !cancelled && reconnect(r),
          onTracks: () => { if (!cancelled) setTracksVersion((v) => v + 1) },
        })
        if (cancelled) { owned.destroy(); return }
        handle.current = owned
        video.muted = !!opts.muted
        try { await video.play() } catch (e) {
          if (!cancelled && e instanceof DOMException && e.name === 'NotAllowedError') setState('paused')
        }
      } catch (e) {
        if (!cancelled) reconnect(e instanceof Error ? e.message : 'unknown error')
      }
    })()
    return () => {
      cancelled = true
      controller.abort()
      clearRetry()
      owned?.destroy()
      if (handle.current === owned) handle.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt])

  useEffect(() => { retry.current = 0 }, [key])

  // media element events
  useEffect(() => {
    if (!key) return
    const v = videoRef.current
    if (!v) return
    const onMeta = () => {
      const d = descRef.current
      if (d && !d.live && d.resume_position_ms > 0 && v.currentTime < 1) v.currentTime = d.resume_position_ms / 1000
    }
    const onPlaying = () => { clearRetry(); setState('playing') }
    const onPause = () => { if (!v.ended) setState((s) => (s === 'playing' ? 'paused' : s)) }
    const onWaiting = () => setState((s) => (s === 'playing' ? 'loading' : s))
    const onError = () => reconnect(v.error?.message || 'media error')
    v.addEventListener('loadedmetadata', onMeta)
    v.addEventListener('playing', onPlaying)
    v.addEventListener('pause', onPause)
    v.addEventListener('waiting', onWaiting)
    v.addEventListener('error', onError)
    return () => {
      v.removeEventListener('loadedmetadata', onMeta); v.removeEventListener('playing', onPlaying); v.removeEventListener('pause', onPause)
      v.removeEventListener('waiting', onWaiting); v.removeEventListener('error', onError)
    }
  }, [videoRef, reconnect, clearRetry, key])

  // A server can connect without delivering decodable frames. Do not leave the
  // viewer on an endless spinner, or reset the retry budget on a single frame.
  useEffect(() => {
    if (!key) return
    if (state === 'playing') {
      const timer = window.setTimeout(() => { retry.current = 0 }, 30_000)
      return () => window.clearTimeout(timer)
    }
    if (state !== 'loading' && state !== 'resolving') return
    const timer = window.setTimeout(() => reconnect('stream timed out'), 25_000)
    return () => window.clearTimeout(timer)
  }, [state, key, attempt, reconnect])

  // progress reporting
  useEffect(() => {
    if (opts.report === false || !req) return
    const v = videoRef.current
    if (!v) return
    const send = (state: 'playing' | 'paused' | 'stopped', unloading = false) => {
      const d = descRef.current
      if (!d || d.mode === 'handoff') return
      reportProgress({
        id: req.id, source_id: req.sourceId ?? null, state,
        position_ms: Math.floor((v.currentTime || 0) * 1000),
        duration_ms: Number.isFinite(v.duration) && !d.live ? Math.floor(v.duration * 1000) : (d.duration_ms ?? null),
      }, unloading)
    }
    const tick = window.setInterval(() => { if (!v.paused && !v.ended) send('playing') }, 10_000)
    const onPause = () => !v.ended && send('paused')
    const onEnded = () => send('stopped')
    const onHide = () => send(v.paused ? 'paused' : 'stopped', true)
    v.addEventListener('pause', onPause)
    v.addEventListener('ended', onEnded)
    window.addEventListener('pagehide', onHide)
    return () => {
      window.clearInterval(tick)
      v.removeEventListener('pause', onPause); v.removeEventListener('ended', onEnded); window.removeEventListener('pagehide', onHide)
      send('stopped', true)
    }
  }, [videoRef, req, opts.report, key])

  const tracks = useCallback(() => {
    void tracksVersion
    const h = handle.current
    return { audio: h?.audioTracks() ?? [], subs: h?.subtitleTracks() ?? [] }
  }, [tracksVersion])

  return {
    desc, state, error, tracks,
    setAudio: (i: number) => { handle.current?.setAudio(i); setTracksVersion((v) => v + 1) },
    setSubtitle: (i: number) => { handle.current?.setSubtitle(i); setTracksVersion((v) => v + 1) },
    retryNow: () => { clearRetry(); retry.current = 0; setAttempt((a) => a + 1) },
  }
}
