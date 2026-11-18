/*
 * The Android TV app shows this web app full screen and plays streams with its own player. The pieces
 * of that bridge with no DOM in them live here, so they are unit-tested on their own (tests/shell.test.mjs);
 * shell.ts wires them to the page. Keep this file free of imports.
 *
 * Surface, version 1. The TV adds window.StreamHubTV before any script runs:
 *   play(json): boolean          start native playback of a ShellPlay; false when the TV refused it
 *   signedOut(reason): void      this web app has no session any more; the TV returns to its pairing screen
 *   capabilities(): string       JSON: { bridge, shell, version_code, native_playback, hdr, video_codecs, audio_passthrough }
 *   deliverToken(id, json): void the answer to window.streamhubShell.token(id, rejected)
 * and calls window.streamhubShell.{ back(), token(id, rejected), playbackEnded() } in the page.
 *
 * Messages (the mini player, more to come), one JSON envelope `{"v":1,"kind":…}` each way:
 *   TV → page:  window.streamhubShell.message(json)        kind "mini_player" (parseShellMessage)
 *   page → TV:  window.StreamHubTV.message(json): boolean  kind "mini_player_command" (miniPlayerCommand)
 * `message` is optional on the TV side: a TV app without it simply never shows the bar.
 */

export const SHELL_BRIDGE_VERSION = 1
/** The layout width the TV gets: what a 1920×1080 desktop browser lays out, not the ~960 CSS px a TV WebView reports. */
export const SHELL_WIDTH = 1920

export interface StreamHubTVBridge {
  play(json: string): boolean
  signedOut(reason: string): void
  capabilities(): string
  deliverToken(id: string, json: string): void
  /** A page → TV message envelope (miniPlayerCommand). Absent on TV apps older than the mini player. */
  message?(json: string): boolean
}

/** The bridge on `w` (the window), or null in every ordinary browser. */
export function bridgeOf(w: unknown): StreamHubTVBridge | null {
  const b = (w as { StreamHubTV?: Partial<StreamHubTVBridge> } | null | undefined)?.StreamHubTV
  if (!b || typeof b !== 'object' && typeof b !== 'function') return null
  return typeof b.play === 'function' && typeof b.signedOut === 'function' && typeof b.capabilities === 'function' && typeof b.deliverToken === 'function'
    ? (b as StreamHubTVBridge) : null
}

/** What the TV is asked to play: the inputs to its own resolve (client=android_tv), never a stream address. */
export interface ShellTitlePlay {
  v: 1
  kind: 'title'
  id: string
  source_id?: string
  variant_id?: string
  allow_unverified_audio?: boolean
  audio_track?: number
  catchup_programme_id?: string
  rewind_at?: string
  start_position_ms?: number
}
export interface ShellRoomPlay { v: 1; kind: 'room'; room_code: string; member_token: string; allow_unverified_audio: boolean }
export type ShellPlay = ShellTitlePlay | ShellRoomPlay

export interface AudioScope { sourceId?: string | null; catchupProgrammeId?: string | null; rewindAt?: string | null }
/** Whether this page granted the one-off audio consent `token` names (player/audioConsent.ts). */
export type ConsentCheck = (token: string | null, id: string, variantId: string | undefined, scope: AudioScope) => boolean

/**
 * `/play/<id>?…` as the player route reads it (screens/Player.tsx): variant, audio_choice (a consent
 * this page granted), audio (a track of that variant), source, catchup, rewind. Null for anything
 * that is not a player address.
 */
export function titlePlay(to: string, consent: ConsentCheck): ShellTitlePlay | null {
  let url: URL
  try { url = new URL(to, 'http://shell.invalid') } catch { return null }
  const m = /^\/play\/([^/]+)$/.exec(url.pathname)
  if (!m) return null
  let id: string
  try { id = decodeURIComponent(m[1]!) } catch { return null }
  if (!id) return null
  const sp = url.searchParams
  const play: ShellTitlePlay = { v: 1, kind: 'title', id }
  const variant = sp.get('variant') || undefined
  const source = sp.get('source')
  const catchup = sp.get('catchup')
  const rewind = sp.get('rewind')
  if (source) play.source_id = source
  if (variant) play.variant_id = variant
  if (catchup) play.catchup_programme_id = catchup
  else if (rewind) play.rewind_at = rewind
  if (variant && consent(sp.get('audio_choice'), id, variant, { sourceId: source, catchupProgrammeId: catchup, rewindAt: rewind })) play.allow_unverified_audio = true
  const audio = sp.get('audio')
  // An audio track belongs to one variant's file; without the variant the index means nothing.
  if (variant && audio !== null && /^\d{1,3}$/.test(audio) && Number(audio) <= 255) play.audio_track = Number(audio)
  return play
}

/** A seat this page took in a watch room, handed to the TV by its member token. */
export const roomPlay = (code: string, memberToken: string, allowUnverifiedAudio: boolean): ShellRoomPlay =>
  ({ v: 1, kind: 'room', room_code: code.toUpperCase(), member_token: memberToken, allow_unverified_audio: allowUnverifiedAudio })

/** The viewport the shell asks for: SHELL_WIDTH CSS px, scaled to fit the screen, no zooming. */
export function shellViewport(screenWidth: number): string {
  const scale = screenWidth > 0 ? Math.min(1, Math.round((screenWidth / SHELL_WIDTH) * 1000) / 1000) : 0.5
  return `width=${SHELL_WIDTH}, initial-scale=${scale}, minimum-scale=${scale}, maximum-scale=${scale}, user-scalable=no, viewport-fit=cover`
}

/** Whether a JWT access token expires within `marginMs` of `now`. A token that is not a readable JWT is taken as current. */
export function expiresSoon(token: string, now: number, marginMs = 30_000): boolean {
  const payload = token.split('.')[1]
  if (!payload) return false
  try {
    const json = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(payload.length / 4) * 4, '='))) as { exp?: unknown }
    return typeof json.exp === 'number' && json.exp * 1000 <= now + marginMs
  } catch { return false }
}

/**
 * The TV's own player while it is in picture-in-picture: what the page's bottom bar shows (null: no bar).
 * The TV sends it whenever any of it changes, and at least every few seconds while it plays.
 */
export interface MiniPlayerState {
  id: string
  title: string
  subtitle: string | null
  image: string | null
  position_ms: number
  duration_ms: number | null
  playing: boolean
  has_next: boolean
  has_previous: boolean
  live: boolean
}
export type MiniPlayerCommandName = 'play_pause' | 'seek_by' | 'next' | 'previous' | 'expand' | 'close'
export type ShellMessage = { kind: 'mini_player'; state: MiniPlayerState | null }

const MAX_MESSAGE_JSON = 16 * 1024
const str = (v: unknown, max: number): string | null => (typeof v === 'string' && v.length <= max ? v : null)
const int = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 7 * 24 * 3600 * 1000 ? v : null)

/**
 * A TV → page message, checked as strictly as the TV checks play(): the version, the kind and every
 * field's type. Null for anything else (unknown kinds included: an older page ignores a newer TV's news).
 * Takes the JSON text, or an object a TV handed over already parsed.
 */
export function parseShellMessage(input: unknown): ShellMessage | null {
  let root: unknown = input
  if (typeof input === 'string') {
    if (input.length > MAX_MESSAGE_JSON) return null
    try { root = JSON.parse(input) } catch { return null }
  }
  if (!root || typeof root !== 'object') return null
  const m = root as Record<string, unknown>
  if (m.v !== SHELL_BRIDGE_VERSION || m.kind !== 'mini_player') return null
  if (m.state === null) return { kind: 'mini_player', state: null }
  const s = m.state as Record<string, unknown> | undefined
  if (!s || typeof s !== 'object') return null
  const id = str(s.id, 512), title = str(s.title, 512)
  const position = int(s.position_ms)
  const duration = s.duration_ms === null || s.duration_ms === undefined ? null : int(s.duration_ms)
  const subtitle = s.subtitle === null || s.subtitle === undefined ? null : str(s.subtitle, 512)
  const image = s.image === null || s.image === undefined ? null : str(s.image, 4096)
  const flags = ['playing', 'has_next', 'has_previous', 'live'] as const
  if (!id || title === null || position === null || flags.some((f) => typeof s[f] !== 'boolean')) return null
  if ((s.duration_ms != null && duration === null) || (s.subtitle != null && subtitle === null) || (s.image != null && image === null)) return null
  // Artwork is shown in an <img>: only web addresses, never script or data the TV did not mean.
  const safeImage = image && (/^https?:\/\//i.test(image) || (image.startsWith('/') && !image.startsWith('//'))) ? image : null
  return {
    kind: 'mini_player',
    state: {
      id, title, subtitle, image: safeImage, position_ms: position, duration_ms: duration,
      playing: s.playing as boolean, has_next: s.has_next as boolean, has_previous: s.has_previous as boolean, live: s.live as boolean,
    },
  }
}

/** A page → TV command for the player in picture-in-picture. `deltaMs` only for seek_by (negative is back). */
export function miniPlayerCommand(command: MiniPlayerCommandName, deltaMs?: number): string {
  return JSON.stringify({ v: SHELL_BRIDGE_VERSION, kind: 'mini_player_command', command, delta_ms: command === 'seek_by' ? Math.trunc(deltaMs ?? 0) : null })
}
