import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { api, ApiError, reportProgress, sessionStands, unwrap } from '../api/client'
import type { PlaybackDescriptor } from '../api/types'
import { useSession } from '../store/session'
import { BufferSession, bufferError, type BufferWindow } from './bufferSession'
import { attachSource, type PlayerHandle } from './engine'
import { audioAllowed, audioSelection, preferredAudioTrack } from './audioPolicy'
import { AWAY_FROM_HOME, busyHeading, parseSourceBusy, streamRefusal, type SourceBusy, type StreamRefusal } from './sourceBusy'

export type PlayState = 'resolving' | 'loading' | 'buffering' | 'playing' | 'paused' | 'reconnecting' | 'error' | 'handoff'

export interface PlaybackReq {
  id: string; variantId?: string; allowUnverifiedAudio?: boolean; sourceId?: string | null; catchupProgrammeId?: string | null; rewindAt?: string | null
  /** Index into the file's probed audio tracks; the server encodes that one track (web VOD). */
  audioTrack?: number | null
  /** Start a restart-at-offset stream here instead of at the profile's resume position. */
  startPositionMs?: number | null
}

async function playbackGeneration(): Promise<string> {
  const status = await unwrap<{ generation: string }>(api.GET('/playback/generation'))
  return status.generation
}

/** Browsers without MPEG-TS playback through Media Source ask the server for HLS. */
export const prefersHls = (): boolean => /iPad|iPhone|iPod/.test(navigator.userAgent) || /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent) || !('MediaSource' in window)

export async function resolve(req: PlaybackReq, generation?: string): Promise<PlaybackDescriptor> {
  const recoveryGeneration = generation ?? await playbackGeneration()
  return unwrap<PlaybackDescriptor>(
    api.POST('/playback/resolve', {
      body: { id: req.id, variant_id: req.variantId, allow_unverified_audio: req.allowUnverifiedAudio ?? false, source_id: req.sourceId ?? null, client: 'web', recovery_generation: recoveryGeneration, prefer_hls: prefersHls(), rewind_at: req.rewindAt ?? null, catchup_programme_id: req.catchupProgrammeId ?? null, audio_track: req.audioTrack ?? null, start_position_ms: req.startPositionMs == null ? null : Math.max(0, Math.floor(req.startPositionMs)) },
    }),
  )
}

const MAX_RETRIES = 6

/**
 * Resolves + attaches a stream to <video>. Reconnects with exponential backoff on fatal errors by re-resolving
 * (fresh proxy token). Reports progress every 10 s, on pause/ended and on page unload.
 *
 * A descriptor with start_position_ms is a restart-at-offset stream (a VOD file the server converts for the
 * browser): its media time zero is that position in the title and duration_ms is the title's length. Seeking
 * outside what the stream has produced, and switching the audio track, resolve again at the wanted position.
 */
export function usePlayback(videoRef: RefObject<HTMLVideoElement | null>, req: PlaybackReq | null, opts: {
  muted?: boolean; report?: boolean; buffer?: boolean
  /** A watch room supplies its member's descriptor (the room decides variant and position) instead of /playback/resolve. */
  descriptor?: (generation: string | undefined) => Promise<PlaybackDescriptor>
} = {}) {
  const descriptorSource = useRef(opts.descriptor)
  descriptorSource.current = opts.descriptor
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
  // The viewer paused. A seek or a track change that needs a new stream then starts it paused, as a seek
  // inside the old one would have left it. A room decides for its members whether they play.
  const pausedByViewer = useRef(false)
  // The viewer's own mute. Unmuting is asked of checkAudio, never done here: a stream whose audio is
  // not allowed for this profile stays silent whatever the mute button says.
  const silenced = useRef(false)
  const [bufferWindow, setBufferWindow] = useState<BufferWindow | null>(null)
  const [bufferIssue, setBufferIssue] = useState<string | null>(null)
  const buffer = useRef<BufferSession | null>(null)
  const pendingRelease = useRef<Promise<void>>(Promise.resolve())
  const seekBufferRef = useRef<((at: string | null) => Promise<void>) | null>(null)
  const [desc, setDesc] = useState<PlaybackDescriptor | null>(null)
  const [state, setState] = useState<PlayState>('resolving')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<SourceBusy | null>(null)
  const plays = useRef(0)
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
  // What the viewer changed since this playback began: applied to every later resolve of the same request.
  const restart = useRef<{ audioTrack?: number; startMs?: number }>({})
  const lastPosition = useRef<{ desc: PlaybackDescriptor | null; ms: number }>({ desc: null, ms: 0 })
  /** Position in the title, which for a restart-at-offset stream is not the media element's time. */
  const titlePositionMs = useCallback(() => {
    const d = descRef.current
    const v = videoRef.current
    // Tearing a player down resets the element to zero before the last report goes out: keep what it last showed.
    if (v && v.readyState > 0) lastPosition.current = { desc: d, ms: Math.floor((d?.start_position_ms ?? 0) + v.currentTime * 1000) }
    return lastPosition.current.desc === d ? lastPosition.current.ms : d?.start_position_ms ?? 0
  }, [videoRef])
  const clearWaiting = useCallback(() => {
    if (waitingTimer.current !== null) window.clearTimeout(waitingTimer.current)
    waitingTimer.current = null
  }, [])
  const clearRetry = useCallback(() => {
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [])
  const key = req ? `${profileId}|${sessionGeneration}|${preferences}|${req.id}|${req.sourceId ?? ''}|${req.catchupProgrammeId ?? ''}|${req.variantId ?? ''}|${req.allowUnverifiedAudio ?? false}|${req.rewindAt ?? ""}|${req.audioTrack ?? ''}` : ''

  // The source has no free connection. Retrying on a timer would only knock again: stop and say who has them.
  const showBusy = useCallback((found: SourceBusy) => {
    terminal.current = true
    clearRetry(); clearWaiting()
    handle.current?.destroy()
    setBusy(found); setState('error'); setError(busyHeading(found))
  }, [clearRetry, clearWaiting])

  // The stream will not play for this user from here (away from home, or no longer allowed): say so, do not knock again.
  const showDenied = useCallback((message: string) => {
    terminal.current = true
    clearRetry(); clearWaiting()
    handle.current?.destroy()
    setBusy(null); setState('error'); setError(message)
  }, [clearRetry, clearWaiting])
  // The stream says the session it was issued to no longer stands. Once per deliberate Play or Retry, have the API
  // judge this device's session: a revoked or suspended one is signed out there, a stale one refreshed, and a
  // session that stands asks for its stream again at once.
  const sessionChecked = useRef<object | null>(null)
  const recheckSession = useCallback((): boolean => {
    const owner = intent.current
    if (sessionChecked.current === owner) return false
    sessionChecked.current = owner
    void sessionStands().then((stands) => {
      if (!stands || intent.current !== owner || terminal.current) return
      clearRetry(); retry.current = 0
      setAttempt((a) => a + 1)
    })
    return true
  }, [clearRetry])
  /** Act on what the stream said. False when it said nothing this can act on, or it was acted on before. */
  const heed = useCallback((refused: StreamRefusal | null): boolean => {
    if (!refused) return false
    if ('busy' in refused) showBusy(refused.busy)
    else if ('denied' in refused) showDenied(refused.denied)
    else return recheckSession()
    return true
  }, [showBusy, showDenied, recheckSession])

  // Most players only report that their stream failed, never the server's answer. When the stream is one
  // of ours, ask for it again: a 409 says who is using the source, a 403 or 401 why this user may not have it.
  const explainRefusal = useCallback(() => {
    const d = descRef.current
    if (!d?.url || (d.mode !== 'hls' && d.mode !== 'proxy')) return
    let url: URL
    try { url = new URL(d.url, window.location.href) } catch { return }
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/stream/')) return
    const owner = intent.current
    const played = plays.current
    fetch(url, { headers: { Range: 'bytes=0-0' }, cache: 'no-store' }).then(async (response) => {
      if (response.status !== 409 && response.status !== 403 && response.status !== 401) { void response.body?.cancel(); return }
      const found = streamRefusal(await response.json())
      // Stale once the viewer moved on, or once a retry got through while this was in flight.
      if (!found || intent.current !== owner || plays.current !== played || terminal.current) return
      heed(found)
    }).catch(() => undefined)
  }, [heed])

  const reconnect = useCallback((_reason: string, refusal?: unknown) => {
    if (terminal.current) return
    clearWaiting()
    if (!buffer.current) {
      const refused = streamRefusal(refusal)
      if (refused && 'session' in refused) setState('reconnecting')
      if (heed(refused)) return
    }
    // A restart-at-offset stream comes back where it stopped, not where progress was last saved.
    if (descRef.current?.start_position_ms != null && hasPlayed.current) restart.current = { ...restart.current, startMs: titlePositionMs() }
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
    explainRefusal()
  }, [clearWaiting, titlePositionMs, explainRefusal, heed])

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
      restart.current = {}
      pausedByViewer.current = false
    }
    consent.current = null
    selectedAudio.current = null
    wantedAudio.current = null
    wantsPlayback.current = !pausedByViewer.current || !!descriptorSource.current
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
    setBusy(null)
    ;(async () => {
      try {
        await pendingRelease.current
        if (cancelled) return
        if (!intent.current.generation) {
          const generation = await playbackGeneration()
          if (cancelled) return
          intent.current.generation = generation
        }
        const d = descriptorSource.current
          ? await descriptorSource.current(intent.current.generation)
          : await resolve({ ...req, audioTrack: restart.current.audioTrack ?? req.audioTrack, startPositionMs: restart.current.startMs ?? req.startPositionMs }, intent.current.generation)
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
            video.muted = !!opts.muted || silenced.current
            if (wantsPlayback.current && video.paused) void video.play().catch(() => { if (!cancelled) setState('paused') })
            // A stream that is meant to stay paused has arrived: it is not still starting.
            else if (!wantsPlayback.current) setState((s) => (s === 'loading' ? 'paused' : s))
            return true
          }
          const next = await attachSource(video, descriptor, {
            signal: attachController.signal,
            onFatal: (r, refusal) => !cancelled && sequence === attachSequence && reconnect(r, refusal),
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
        const refused = e instanceof ApiError ? parseSourceBusy(e.body) : null
        if (refused) { showBusy(refused); return }
        // Re-resolving cannot repair missing content or denied access. Keep
        // transient network/server failures on the bounded reconnect path.
        // A recording that cannot be played is refused for a reason only the server knows: say it, do not knock again.
        const recording = req.id.startsWith('rec:')
        if (e instanceof ApiError && ([400, 401, 403, 404, 410, 422].includes(e.status) || e.code === 'audio_selection_required' || (recording && e.status >= 400 && e.status < 500))) {
          terminal.current = true
          clearWaiting()
          clearRetry()
          setState('error')
          // A room's refusals (the room ended, it is full) are the server's own words.
          setError((descriptorSource.current && e.code?.startsWith('room_')) || (recording && e.message) ? e.message : e.code === 'audio_selection_required' ? 'Choose an audio language and source from title details before playing.' : e.code === 'playback_paused' || e.code === 'playback_stopped'
            ? 'Playback was stopped by an administrator. Once playback is resumed, choose Try again to start a new stream.'
            : e.code === 'playback_reload_required'
            ? 'Playback controls have been updated. Reload Streamhub before trying again.'
            : e.code === 'remote_access_denied'
            ? AWAY_FROM_HOME
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
      // A restart-at-offset stream already begins at the resume position.
      if (d && !d.live && d.start_position_ms == null && d.resume_position_ms > 0 && v.currentTime < 1) v.currentTime = d.resume_position_ms / 1000
    }
    // The source can drop mid-film and ffmpeg then closes the stream as if it had ended. Carry on from there.
    const onEnded = () => {
      const d = descRef.current
      if (d?.start_position_ms == null || !d.duration_ms || terminal.current) return
      const at = titlePositionMs()
      if (at < d.duration_ms - 30_000) { restart.current = { ...restart.current, startMs: at }; setAttempt((a) => a + 1) }
    }
    const onPlaying = () => { if (terminal.current || !checkAudio.current()) { v.muted = true; v.pause(); return }; hasPlayed.current = true; plays.current++; clearWaiting(); clearRetry(); setState('playing') }
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
    v.addEventListener('ended', onEnded)
    v.addEventListener('timeupdate', titlePositionMs)
    return () => {
      clearWaiting()
      v.removeEventListener('loadedmetadata', onMeta); v.removeEventListener('playing', onPlaying); v.removeEventListener('pause', onPause)
      v.removeEventListener('waiting', onWaiting); v.removeEventListener('error', onError); v.removeEventListener('ended', onEnded); v.removeEventListener('timeupdate', titlePositionMs)
    }
  }, [videoRef, reconnect, clearRetry, clearWaiting, key, titlePositionMs])

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
        position_ms: titlePositionMs(),
        // The media element only knows the part of a restart-at-offset stream produced so far.
        duration_ms: d.start_position_ms != null ? (d.duration_ms ?? null) : Number.isFinite(v.duration) && !d.live ? Math.floor(v.duration * 1000) : (d.duration_ms ?? null),
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
  }, [videoRef, req, opts.report, key, profileId, sessionGeneration, titlePositionMs])

  const tracks = useCallback(() => {
    void tracksVersion
    const h = handle.current
    return { audio: h?.audioTracks() ?? [], subs: h?.subtitleTracks() ?? [] }
  }, [tracksVersion])

  return {
    desc, state, error, busy, tracks, audioBlocked,
    pause: () => { wantsPlayback.current = false; pausedByViewer.current = true; videoRef.current?.pause() },
    resume: () => { wantsPlayback.current = true; pausedByViewer.current = false; checkAudio.current() },
    consentToCurrentAudio: () => { consent.current = audioSelection(handle.current?.audioTracks() ?? []); wantsPlayback.current = true; pausedByViewer.current = false; checkAudio.current() },
    bufferWindow, bufferIssue, seekBuffer: (at: string | null) => seekBufferRef.current?.(at),
    setAudio: (i: number) => {
      if (!handle.current?.audioTracks().some(t => t.id === i)) return
      const video = videoRef.current
      if (video) { video.muted = true; video.pause() }
      consent.current = null; selectedAudio.current = i; wantsPlayback.current = true; pausedByViewer.current = false
      handle.current.setAudio(i); setTracksVersion((v) => v + 1); checkAudio.current()
    },
    setSubtitle: (i: number) => { handle.current?.setSubtitle(i); setTracksVersion((v) => v + 1) },
    /** The viewer's mute button. */
    setSilenced: (on: boolean) => {
      silenced.current = on
      const video = videoRef.current
      if (!video) return
      if (on) video.muted = true
      // Every start of playback goes through checkAudio, which applies this; only a stream already playing needs telling.
      else if (!video.paused) checkAudio.current()
    },
    retryNow: () => { clearWaiting(); clearRetry(); terminal.current = false; pausedByViewer.current = false; intent.current = { key }; retry.current = 0; setAttempt((a) => a + 1) },
    /** Restart-at-offset streams: start a new stream at this position in the title. */
    seekTo: (ms: number) => {
      if (descRef.current?.start_position_ms == null || terminal.current) return
      clearWaiting(); clearRetry(); retry.current = 0
      restart.current = { ...restart.current, startMs: Math.max(0, Math.floor(ms)) }
      setAttempt((a) => a + 1)
    },
    /** Attach again with a fresh descriptor (a watch room whose stream was restarted). */
    reload: () => {
      if (terminal.current) return
      clearWaiting(); clearRetry(); retry.current = 0
      setAttempt((a) => a + 1)
    },
    /** Have the server encode another audio track of the file, continuing from the current position. */
    selectAudioTrack: (index: number) => {
      const d = descRef.current
      if (!d || d.audio_track == null || d.audio_track === index || terminal.current) return
      clearWaiting(); clearRetry(); retry.current = 0
      restart.current = { audioTrack: index, startMs: titlePositionMs() }
      setAttempt((a) => a + 1)
    },
  }
}
