import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { api, reportProgress, unwrap } from '../api/client'
import type { PlaybackDescriptor } from '../api/types'
import { attachSource, type PlayerHandle } from './engine'

export type PlayState = 'resolving' | 'loading' | 'playing' | 'paused' | 'reconnecting' | 'error' | 'handoff'

export interface PlaybackReq { id: string; sourceId?: string | null; catchupProgrammeId?: string | null }

export async function resolve(req: PlaybackReq): Promise<PlaybackDescriptor> {
  return unwrap<PlaybackDescriptor>(
    api.POST('/playback/resolve', {
      body: { id: req.id, source_id: req.sourceId ?? null, client: 'web', catchup_programme_id: req.catchupProgrammeId ?? null },
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
  const key = req ? `${req.id}|${req.sourceId ?? ''}|${req.catchupProgrammeId ?? ''}` : ''

  const reconnect = useCallback((reason: string) => {
    if (retry.current >= MAX_RETRIES) {
      setState('error')
      setError(`Playback failed: ${reason}`)
      return
    }
    const wait = Math.min(15_000, 500 * 2 ** retry.current++)
    setState('reconnecting')
    window.setTimeout(() => setAttempt((a) => a + 1), wait)
  }, [])

  // resolve + attach
  useEffect(() => {
    if (!req) return
    let cancelled = false
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
        handle.current = await attachSource(video, d, {
          onFatal: (r) => !cancelled && reconnect(r),
          onTracks: () => setTracksVersion((v) => v + 1),
        })
        if (cancelled) { handle.current.destroy(); handle.current = null; return }
        video.muted = !!opts.muted
        try { await video.play() } catch { /* autoplay blocked: user presses play */ }
      } catch (e) {
        if (!cancelled) reconnect(e instanceof Error ? e.message : 'unknown error')
      }
    })()
    return () => {
      cancelled = true
      handle.current?.destroy()
      handle.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt])

  useEffect(() => { retry.current = 0 }, [key])

  // media element events
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onMeta = () => {
      const d = descRef.current
      if (d && !d.live && d.resume_position_ms > 0 && v.currentTime < 1) v.currentTime = d.resume_position_ms / 1000
    }
    const onPlaying = () => { retry.current = 0; setState('playing') }
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
  }, [videoRef, reconnect])

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
    retryNow: () => { retry.current = 0; setAttempt((a) => a + 1) },
  }
}
