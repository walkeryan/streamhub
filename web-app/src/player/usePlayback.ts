import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { api, ApiError, reportProgress, unwrap } from '../api/client'
import type { PlaybackDescriptor } from '../api/types'
import { useSession } from '../store/session'
import { BufferSession, bufferError, type BufferWindow } from './bufferSession'
import { attachSource, type PlayerHandle } from './engine'
import { audioAllowed, audioSelection, preferredAudioTrack } from './audioPolicy'

export type PlayState = 'resolving' | 'loading' | 'buffering' | 'playing' | 'paused' | 'reconnecting' | 'error' | 'handoff'

export interface PlaybackReq { id: string; variantId?: string; allowUnverifiedAudio?: boolean; sourceId?: string | null; catchupProgrammeId?: string | null; rewindAt?: string | null }

async function playbackGeneration(): Promise<string> {
  const status = await unwrap<{ generation: string }>(api.GET('/playback/generation'))
  return status.generation
}

export async function resolve(req: PlaybackReq, generation?: string): Promise<PlaybackDescriptor> {
  const recoveryGeneration = generation ?? await playbackGeneration()
  return unwrap<PlaybackDescriptor>(
    api.POST('/playback/resolve', {
      body: { id: req.id, variant_id: req.variantId, allow_unverified_audio: req.allowUnverifiedAudio ?? false, source_id: req.sourceId ?? null, client: 'web', recovery_generation: recoveryGeneration, prefer_hls: /iPad|iPhone|iPod/.test(navigator.userAgent) || /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent) || !('MediaSource' in window), rewind_at: req.rewindAt ?? null, catchup_programme_id: req.catchupProgrammeId ?? null },
    }),
  )
}

const MAX_RETRIES = 6

/**
 * Resolves + attaches a stream to <video>. Reconnects with exponential backoff on fatal errors by re-resolving
 * (fresh proxy token). Reports progress every 10 s, on pause/ended and on page unload.
 */
export function usePlayback(videoRef: RefObject<HTMLVideoElement | null>, req: PlaybackReq | null, opts: { muted?: boolean; report?: boolean; buffer?: boolean } = {}) {
  const profile = useSession((s) => s.profile)
  const profileId = profile?.id
  const sessionGeneration = useSession((s) => s.generation)
  const preferences = JSON.stringify(profile?.languages ?? ['en'])
  const [audioBlocked, setAudioBlocked] = useState(false)
  const checkAudio = useRef<() => boolean>(() => false)
  const consent = useRef<string | null>(null)
  const selectedAudio = useRef<number | null>(null)
  const wantedAudio = useRef<number | null>(null)
  const wantsPlayback = useRef(true)
  const [bufferWindow, setBufferWindow] = useState<BufferWindow | null>(null)
  const [bufferIssue, setBufferIssue] = useState<string | null>(null)
  const buffer = useRef<BufferSession | null>(null)
  const pendingRelease = useRef<Promise<void>>(Promise.resolve())
  const seekBufferRef = useRef<((at: string | null) => Promise<void>) | null>(null)
  const [desc, setDesc] = useState<PlaybackDescriptor | null>(null)
  const [state, setState] = useState<PlayState>('resolving')
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [tracksVersion, setTracksVersion] = useState(0)
  const handle = useRef<PlayerHandle | null>(null)
  const descRef = useRef<PlaybackDescriptor | null>(null)
  const descriptorOwner = useRef<{ key: string; profileId: string | undefined; generation: number } | null>(null)
  const retry = useRef(0)
  const retryTimer = useRef<number | null>(null)
  const waitingTimer = useRef<number | null>(null)
  const hasPlayed = useRef(false)
  const terminal = useRef(false)
  const intent = useRef<{ key: string; generation?: string }>({ key: '' })
  const clearWaiting = useCallback(() => {
    if (waitingTimer.current !== null) window.clearTimeout(waitingTimer.current)
    waitingTimer.current = null
  }, [])
  const clearRetry = useCallback(() => {
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [])
  const key = req ? `${profileId}|${sessionGeneration}|${preferences}|${req.id}|${req.sourceId ?? ''}|${req.catchupProgrammeId ?? ''}|${req.variantId ?? ''}|${req.allowUnverifiedAudio ?? false}|${req.rewindAt ?? ""}` : ''

  const reconnect = useCallback((_reason: string) => {
    if (terminal.current) return
    clearWaiting()
    if (buffer.current) {
      terminal.current = true
      handle.current?.destroy()
      pendingRelease.current = buffer.current.release()
      buffer.current = null
      setBufferWindow(null)
      setState('error')
      setError('Buffered playback stopped. Choose Try again to start a new playback session.')
      return
    }
    if (retryTimer.current !== null) return
    if (retry.current >= MAX_RETRIES) {
      setState('error')
      setError('The stream could not start or lost its connection. Try again or choose another channel.')
      return
    }
    const wait = Math.min(15_000, 500 * 2 ** retry.current++)
    setState('reconnecting')
    retryTimer.current = window.setTimeout(() => { retryTimer.current = null; setAttempt((a) => a + 1) }, wait)
  }, [clearWaiting])

  // resolve + attach
  useEffect(() => {
    if (!req) {
      intent.current = { key: '' }
      terminal.current = false
      return
    }
    if (intent.current.key !== key) {
      intent.current = { key }
      terminal.current = false
    }
    consent.current = null
    selectedAudio.current = null
    wantedAudio.current = null
    wantsPlayback.current = true
    setAudioBlocked(false)
    let cancelled = false
    const ownsSession = () => { const session = useSession.getState(); return session.profile?.id === profileId && session.generation === sessionGeneration }
    const controller = new AbortController()
    hasPlayed.current = false
    clearWaiting()
    let owned: PlayerHandle | null = null
    let lease: BufferSession | null = null
    let attachController = new AbortController()
    let attachSequence = 0
    let pauseHeartbeat: (() => void) | null = null
    setBufferWindow(null)
    setBufferIssue(null)
    const video = videoRef.current
    const releaseOnHide = () => { if (lease) pendingRelease.current = lease.release() }
    window.addEventListener('pagehide', releaseOnHide)
    setState((s) => (s === 'reconnecting' ? s : 'resolving'))
    setError(null)
    ;(async () => {
      try {
        await pendingRelease.current
        if (cancelled) return
        if (!intent.current.generation) {
          const generation = await playbackGeneration()
          if (cancelled) return
          intent.current.generation = generation
        }
        const d = await resolve(req, intent.current.generation)
        if (cancelled || !ownsSession()) return
        setDesc(d)
        descRef.current = d
        descriptorOwner.current = { key, profileId, generation: sessionGeneration }
        if (d.mode === 'handoff') { setState('handoff'); return }
        if (!video) return
        const attach = async (descriptor: PlaybackDescriptor) => {
          const sequence = ++attachSequence
          attachController.abort()
          attachController = new AbortController()
          setState('loading')
          video.muted = true
          video.pause()
          handle.current?.destroy()
          owned = null
          consent.current = null
          selectedAudio.current = null
          wantedAudio.current = null
          let initialConsent = !!req.allowUnverifiedAudio
          // New descriptors carry the exact server policy; older compatible
          // servers still use this profile's preferences. Consent is never a
          // blanket exemption for later track changes.
          const preferred = (descriptor as PlaybackDescriptor & { preferred_audio_languages?: string[] }).preferred_audio_languages ?? JSON.parse(preferences) as string[]
          checkAudio.current = () => {
            const session = useSession.getState()
            if (cancelled || sequence !== attachSequence || !owned || terminal.current || session.profile?.id !== profileId || session.generation !== sessionGeneration) return false
            if (owned.audioReady?.() === false) { video.muted = true; video.pause(); return false }
            const tracks = owned.audioTracks()
            const selection = audioSelection(tracks)
            if (initialConsent && selection !== null && owned.audioReady?.() !== false) { consent.current = selection; initialConsent = false }
            if (selectedAudio.current !== null && tracks.some(t => t.id === selectedAudio.current && t.active)) {
              consent.current = selection; selectedAudio.current = null
            }
            // A successful strict server preflight remains authoritative when the
            // browser exposes no tracks; this does not verify their language.
            const allowed = !!opts.muted || (tracks.length === 0 && owned.audioReady?.() !== false) || audioAllowed(preferred, tracks, consent.current)
            if (!allowed) {
              video.muted = true; video.pause(); clearWaiting(); setAudioBlocked(true); setState('paused')
              if (selectedAudio.current === null) {
                const match = preferredAudioTrack(preferred, tracks)
                if (match !== null && wantedAudio.current !== match) { wantedAudio.current = match; owned.setAudio(match) }
              }
              return false
            }
            setAudioBlocked(false)
            video.muted = !!opts.muted
            if (wantsPlayback.current && video.paused) void video.play().catch(() => { if (!cancelled) setState('paused') })
            return true
          }
          const next = await attachSource(video, descriptor, {
            signal: attachController.signal,
            onFatal: (r) => !cancelled && sequence === attachSequence && reconnect(r),
            onTracks: (reset) => { if (!cancelled && sequence === attachSequence) {
              if (reset) { consent.current = null; selectedAudio.current = null; wantedAudio.current = null }
              setTracksVersion((v) => v + 1); checkAudio.current()
            } },
          })
          if (cancelled || !ownsSession() || sequence !== attachSequence) { next.destroy(); return }
          owned = next
          handle.current = next
          checkAudio.current()
        }
        if (opts.buffer === true && d.buffer_available && d.live && d.mode === 'hls') {
          try {
            lease = await BufferSession.acquire(d, controller.signal)
            if (cancelled) { await lease.release(); return }
            buffer.current = lease
            setBufferWindow(lease.window)
            pauseHeartbeat = lease.startHeartbeat(() => video.paused, (value) => {
              if (!cancelled) setBufferWindow(value)
            }, (failure) => {
              if (cancelled) return
              terminal.current = true
              clearRetry(); clearWaiting()
              owned?.destroy()
              setState('error'); setError(bufferError(failure)); setBufferWindow(null)
            })
            video.addEventListener('pause', pauseHeartbeat)
            seekBufferRef.current = async (at) => {
              if (!lease || cancelled) return
              try {
                const result = await lease.seek(at)
                if (cancelled) return
                setBufferWindow(result.window); setBufferIssue(null)
                await attach({ ...d, url: result.url })
              } catch (failure) {
                if (!cancelled && !(failure instanceof DOMException && failure.name === 'AbortError')) setBufferIssue(bufferError(failure))
              }
            }
          } catch (failure) {
            if (cancelled) return
            // Access/recovery failures must never fall back to an unleased stream.
            terminal.current = true
            setState('error'); setError(bufferError(failure))
            return
          }
        }
        await attach(d)
      } catch (e) {
        if (cancelled) return
        // Re-resolving cannot repair missing content or denied access. Keep
        // transient network/server failures on the bounded reconnect path.
        if (e instanceof ApiError && ([400, 401, 403, 404, 410, 422].includes(e.status) || e.code === 'audio_selection_required')) {
          terminal.current = true
          clearWaiting()
          clearRetry()
          setState('error')
          setError(e.code === 'audio_selection_required' ? 'Choose an audio language and source from title details before playing.' : e.code === 'playback_paused' || e.code === 'playback_stopped'
            ? 'Playback was stopped by an administrator. Once playback is resumed, choose Try again to start a new stream.'
            : e.code === 'playback_reload_required'
            ? 'Playback controls have been updated. Reload Streamhub before trying again.'
            : e.status === 401 || e.status === 403
            ? 'Playback access was denied. Check your sign-in and source access, then try again.'
            : 'This title or channel is unavailable from this source. Try another item or retry after the source is available.')
        } else reconnect(e instanceof Error ? e.message : 'unknown error')
      }
    })()
    return () => {
      cancelled = true
      checkAudio.current = () => false
      if (video) { video.muted = true; video.pause() }
      controller.abort()
      window.removeEventListener('pagehide', releaseOnHide)
      attachController.abort()
      seekBufferRef.current = null
      if (pauseHeartbeat) video?.removeEventListener('pause', pauseHeartbeat)
      if (lease) pendingRelease.current = lease.release()
      if (buffer.current === lease) buffer.current = null
      clearRetry()
      clearWaiting()
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
    const onPlaying = () => { if (terminal.current || !checkAudio.current()) { v.muted = true; v.pause(); return }; hasPlayed.current = true; clearWaiting(); clearRetry(); setState('playing') }
    const onPause = () => { clearWaiting(); if (!v.ended) setState((s) => (s === 'playing' || s === 'buffering' ? 'paused' : s)) }
    const onWaiting = () => {
      if (!hasPlayed.current || v.paused || waitingTimer.current !== null) return
      // Short packet gaps are normal; avoid flashing a startup banner over decoded video.
      waitingTimer.current = window.setTimeout(() => {
        waitingTimer.current = null
        setState((s) => s === 'playing' ? 'buffering' : s)
      }, 500)
    }
    const onError = () => reconnect(v.error?.message || 'media error')
    v.addEventListener('loadedmetadata', onMeta)
    v.addEventListener('playing', onPlaying)
    v.addEventListener('pause', onPause)
    v.addEventListener('waiting', onWaiting)
    v.addEventListener('error', onError)
    return () => {
      clearWaiting()
      v.removeEventListener('loadedmetadata', onMeta); v.removeEventListener('playing', onPlaying); v.removeEventListener('pause', onPause)
      v.removeEventListener('waiting', onWaiting); v.removeEventListener('error', onError)
    }
  }, [videoRef, reconnect, clearRetry, clearWaiting, key])

  // A server can connect without delivering decodable frames. Do not leave the
  // viewer on an endless spinner, or reset the retry budget on a single frame.
  useEffect(() => {
    if (!key) return
    if (state === 'playing') {
      const timer = window.setTimeout(() => { retry.current = 0 }, 30_000)
      return () => window.clearTimeout(timer)
    }
    if (state !== 'loading' && state !== 'resolving' && state !== 'buffering') return
    const timer = window.setTimeout(() => reconnect('stream timed out'), 25_000)
    return () => window.clearTimeout(timer)
  }, [state, key, attempt, reconnect])

  // progress reporting
  useEffect(() => {
    if (opts.report === false || !req) return
    const v = videoRef.current
    if (!v) return
    const send = (state: 'playing' | 'paused' | 'stopped', unloading = false) => {
      const session = useSession.getState()
      const owner = descriptorOwner.current
      if (!profileId || session.profile?.id !== profileId || session.generation !== sessionGeneration || owner?.key !== key || owner.profileId !== profileId || owner.generation !== sessionGeneration) return
      const d = descRef.current
      if (!d || d.mode === 'handoff') return
      const progress = {
        id: req.id, source_id: req.sourceId ?? null, state, expected_profile_id: profileId,
        position_ms: Math.floor((v.currentTime || 0) * 1000),
        duration_ms: Number.isFinite(v.duration) && !d.live ? Math.floor(v.duration * 1000) : (d.duration_ms ?? null),
      }
      reportProgress(progress, unloading)
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
  }, [videoRef, req, opts.report, key, profileId, sessionGeneration])

  const tracks = useCallback(() => {
    void tracksVersion
    const h = handle.current
    return { audio: h?.audioTracks() ?? [], subs: h?.subtitleTracks() ?? [] }
  }, [tracksVersion])

  return {
    desc, state, error, tracks, audioBlocked,
    pause: () => { wantsPlayback.current = false; videoRef.current?.pause() },
    resume: () => { wantsPlayback.current = true; checkAudio.current() },
    consentToCurrentAudio: () => { consent.current = audioSelection(handle.current?.audioTracks() ?? []); wantsPlayback.current = true; checkAudio.current() },
    bufferWindow, bufferIssue, seekBuffer: (at: string | null) => seekBufferRef.current?.(at),
    setAudio: (i: number) => {
      if (!handle.current?.audioTracks().some(t => t.id === i)) return
      const video = videoRef.current
      if (video) { video.muted = true; video.pause() }
      consent.current = null; selectedAudio.current = i; wantsPlayback.current = true
      handle.current.setAudio(i); setTracksVersion((v) => v + 1); checkAudio.current()
    },
    setSubtitle: (i: number) => { handle.current?.setSubtitle(i); setTracksVersion((v) => v + 1) },
    retryNow: () => { clearWaiting(); clearRetry(); terminal.current = false; intent.current = { key }; retry.current = 0; setAttempt((a) => a + 1) },
  }
}
