import type { PlaybackDescriptor } from '../api/types'
import type shakaNs from 'shaka-player'

export type EngineKind = 'hls' | 'mpegts' | 'dash' | 'native'

export interface TrackInfo { id: number; label: string; active: boolean; language?: string }

export interface PlayerHandle {
  kind: EngineKind
  destroy: () => void
  audioTracks: () => TrackInfo[]
  /** False while an engine is still discovering its initial track list. */
  audioReady?: () => boolean
  setAudio: (id: number) => void
  subtitleTracks: () => TrackInfo[]
  /** -1 turns subtitles off */
  setSubtitle: (id: number) => void
}

export interface AttachOptions {
  signal?: AbortSignal
  onFatal: (reason: string) => void
  onTracks?: (resetAudio?: boolean) => void
}

/** Pick an engine from descriptor.mime (falls back to the URL extension). */
export function pickEngine(d: Pick<PlaybackDescriptor, 'mime' | 'url'>): EngineKind {
  const mime = (d.mime ?? '').toLowerCase()
  const path = (d.url ?? '').split('?')[0]?.toLowerCase() ?? ''
  if (mime.includes('mpegurl') || (!mime && path.endsWith('.m3u8'))) return 'hls'
  if (mime === 'video/mp2t' || mime === 'video/mpeg' || (!mime && (path.endsWith('.ts') || path.endsWith('.mts')))) return 'mpegts'
  if (mime.includes('dash') || (!mime && path.endsWith('.mpd'))) return 'dash'
  return 'native'
}

const isSafari = () => /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent)

const noTracks = { audioTracks: () => [], setAudio: () => undefined, subtitleTracks: () => [], setSubtitle: () => undefined }

function nativeTextTracks(video: HTMLVideoElement) {
  return {
    subtitleTracks: (): TrackInfo[] =>
      Array.from(video.textTracks)
        .filter((t) => t.kind === 'subtitles' || t.kind === 'captions')
        .map((t, i) => ({ id: i, label: t.label || t.language || `Track ${i + 1}`, active: t.mode === 'showing' })),
    setSubtitle: (id: number) => {
      const list = Array.from(video.textTracks).filter((t) => t.kind === 'subtitles' || t.kind === 'captions')
      list.forEach((t, i) => (t.mode = i === id ? 'showing' : 'disabled'))
    },
  }
}

export async function attachSource(video: HTMLVideoElement, d: PlaybackDescriptor, opt: AttachOptions): Promise<PlayerHandle> {
  const url = d.url
  if (!url) throw new Error('No stream URL in playback descriptor')
  const kind = pickEngine(d)
  const live = !!d.live

  if (kind === 'hls') {
    const { default: Hls } = await import('hls.js')
    opt.signal?.throwIfAborted()
    if (isSafari() && video.canPlayType('application/vnd.apple.mpegurl') || !Hls.isSupported()) {
      video.src = url
      return { kind: 'native', destroy: () => { video.removeAttribute('src'); video.load() }, ...noTracks, ...nativeTextTracks(video) }
    }
    const hls = new Hls({ lowLatencyMode: live, enableWorker: true, backBufferLength: live ? 30 : 90 })
    let netRetries = 0
    let confirmedAudio = -1
    let audioReady = false
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR && netRetries++ < 2) hls.startLoad()
      else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && netRetries++ < 3) hls.recoverMediaError()
      else opt.onFatal(`${data.type}: ${data.details}`)
    })
    hls.on(Hls.Events.MANIFEST_PARSED, () => { audioReady = true; opt.onTracks?.() })
    hls.on(Hls.Events.AUDIO_TRACK_SWITCHING, () => { confirmedAudio = -1; opt.onTracks?.() })
    hls.on(Hls.Events.AUDIO_TRACK_SWITCHED, (_event, data) => { confirmedAudio = data.id; opt.onTracks?.() })
    hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => { confirmedAudio = -1; opt.onTracks?.(true) })
    hls.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, () => opt.onTracks?.())
    hls.loadSource(url)
    hls.attachMedia(video)
    return {
      kind: 'hls',
      audioReady: () => audioReady,
      destroy: () => hls.destroy(),
      audioTracks: () => hls.audioTracks.map((t, i) => ({ id: i, label: t.name || t.lang || `Audio ${i + 1}`, language: t.lang, active: confirmedAudio === i })),
      setAudio: (id) => { hls.audioTrack = id },
      subtitleTracks: () => hls.subtitleTracks.map((t, i) => ({ id: i, label: t.name || t.lang || `Subtitles ${i + 1}`, active: hls.subtitleTrack === i })),
      setSubtitle: (id) => { hls.subtitleTrack = id; hls.subtitleDisplay = id >= 0 },
    }
  }

  if (kind === 'mpegts') {
    const { default: mpegts } = await import('mpegts.js')
    opt.signal?.throwIfAborted()
    if (!mpegts.isSupported()) throw new Error('This browser cannot play MPEG-TS streams')
    const p = mpegts.createPlayer(
      { type: 'mpegts', isLive: live, url },
      // Keep the library defaults for packet buffering and live-latency chasing.
      { enableStashBuffer: true, liveBufferLatencyChasing: false, isLive: live, lazyLoad: false },
    )
    p.on(mpegts.Events.ERROR, (type: string, detail: string) => opt.onFatal(`${type}: ${detail}`))
    p.attachMediaElement(video)
    p.load()
    return { kind: 'mpegts', destroy: () => { try { p.unload(); p.detachMediaElement(); p.destroy() } catch { /* already gone */ } }, ...noTracks, ...nativeTextTracks(video) }
  }

  if (kind === 'dash') {
    const mod = await import('shaka-player')
    opt.signal?.throwIfAborted()
    const shaka = ((mod as unknown as { default?: typeof shakaNs }).default ?? mod) as typeof shakaNs
    shaka.polyfill.installAll()
    if (!shaka.Player.isBrowserSupported()) throw new Error('This browser cannot play DASH streams')
    const player = new shaka.Player()
    await player.attach(video)
    player.addEventListener('error', (e: Event) => opt.onFatal(`dash: ${String((e as unknown as { detail?: { code?: number } }).detail?.code ?? 'error')}`))
    player.addEventListener('trackschanged', () => opt.onTracks?.(true))
    player.addEventListener('variantchanged', () => opt.onTracks?.())
    try {
      opt.signal?.throwIfAborted()
      await player.load(url)
      opt.signal?.throwIfAborted()
    } catch (e) { await player.destroy(); throw e }
    return {
      kind: 'dash',
      destroy: () => { void player.destroy() },
      audioTracks: () => player.getAudioTracks().map((t, i) => ({ id: i, label: t.label || t.language || `Audio ${i + 1}`, language: t.language, active: t.active })),
      setAudio: (id) => { const t = player.getAudioTracks()[id]; if (t) player.selectAudioTrack(t) },
      subtitleTracks: () => player.getTextTracks().map((t, i) => ({ id: i, label: t.label || t.language || `Subtitles ${i + 1}`, active: t.active })),
      setSubtitle: (id) => { player.selectTextTrack(player.getTextTracks()[id] ?? null) },
    }
  }

  // mp4 / webm / anything the browser decodes natively
  video.src = url
  return { kind: 'native', destroy: () => { video.removeAttribute('src'); video.load() }, ...noTracks, ...nativeTextTracks(video) }
}
