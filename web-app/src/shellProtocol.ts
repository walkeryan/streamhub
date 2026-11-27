/*
 * The Android TV app shows this web app full screen and plays streams with its own player. The pieces
 * of that bridge with no DOM in them live here, so they are unit-tested on their own (tests/shell.test.mjs);
 * shell.ts wires them to the page. Keep this file free of imports.
 *
 * Surface, version 1. The TV adds window.StreamHubTV before any script runs:
 *   play(json): boolean          start native playback of a ShellPlay; false when the TV refused it
 *   signedOut(reason): void      this web app has no session any more; the TV returns to its pairing screen
 *   capabilities(): string       JSON: { bridge, shell, version_code, native_playback, hdr, video_codecs, audio_passthrough, mini_player, voice_search }
 *   deliverToken(id, json): void the answer to window.streamhubShell.token(id, rejected)
 * and calls window.streamhubShell.{ back(), token(id, rejected), playbackEnded() } in the page.
 *
 * Messages, one JSON envelope `{"v":1,"kind":…}` each way:
 *   TV → page:  window.streamhubShell.message(json)        kinds "mini_player", "voice", "ime" (parseShellMessage)
 *   page → TV:  window.StreamHubTV.message(json): boolean  kinds "mini_player_command" (miniPlayerCommand),
 *                                                          "voice_command" (voiceCommand)
 * `message` is optional on the TV side: a TV app without it simply never shows the bar, nor a microphone.
 * Voice search is offered only when capabilities() says `voice_search: true`.
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
  v: 1 | 2
  buffer_at?: string
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
  const buffered = sp.get('buffer_at')
  if (buffered && id.startsWith('ch:')) { play.v = 2; play.buffer_at = buffered }
  if (id.startsWith('rec:') && sp.get('recording_start') === '1') play.start_position_ms = 0
  const scene = sp.get('scene_ms')
  if (variant && scene && /^\d{1,9}$/.test(scene) && Number(scene) <= 604800000) play.start_position_ms = Number(scene)
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
  /** The TV takes this page's keep-clear areas (`keep_clear` messages; Android 13 and later). */
  keep_clear: boolean
  /** Where the picture-in-picture window is, in this page's CSS px; null when the TV cannot tell. */
  pip_rect: CssRect | null
}
export interface CssRect { x: number; y: number; w: number; h: number }
export type MiniPlayerCommandName = 'play_pause' | 'seek_by' | 'next' | 'previous' | 'expand' | 'close'
/**
 * Voice search on the TV (its speech recogniser, started by `voice_command` or by the remote's search
 * key): `listening` when it starts, `partial` with the words so far, `final` with what was said (the page
 * searches for it), `error` with a reason, `idle` when it stopped without words.
 */
export type VoiceState = 'listening' | 'partial' | 'final' | 'error' | 'idle'
export type VoiceError = 'no_match' | 'unavailable' | 'permission' | 'busy' | 'network' | 'other'
export interface VoiceEvent { state: VoiceState; text: string | null; error: VoiceError | null }
export type ShellMessage =
  | { kind: 'mini_player'; state: MiniPlayerState | null }
  | ({ kind: 'voice' } & VoiceEvent)
  /** The TV's on-screen keyboard opened or closed (closed: whatever was being typed stops being edited). */
  | { kind: 'ime'; visible: boolean }

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
  if (m.v !== SHELL_BRIDGE_VERSION) return null
  if (m.kind === 'voice') return parseVoice(m)
  if (m.kind === 'ime') return typeof m.visible === 'boolean' ? { kind: 'ime', visible: m.visible } : null
  if (m.kind !== 'mini_player') return null
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
  // Newer TVs add these two; an older TV leaves them out, which reads as "no keep-clear, window unknown".
  const r = s.pip_rect as Record<string, unknown> | null | undefined
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 100_000 ? v : null)
  const pip = r && typeof r === 'object' ? [num(r.x), num(r.y), num(r.w), num(r.h)] : null
  const pipRect = pip && pip.every((n) => n !== null) && (pip[2] as number) > 0 && (pip[3] as number) > 0
    ? { x: pip[0] as number, y: pip[1] as number, w: pip[2] as number, h: pip[3] as number } : null
  return {
    kind: 'mini_player',
    state: {
      id, title, subtitle, image: safeImage, position_ms: position, duration_ms: duration,
      playing: s.playing as boolean, has_next: s.has_next as boolean, has_previous: s.has_previous as boolean, live: s.live as boolean,
      keep_clear: s.keep_clear === true, pip_rect: pipRect,
    },
  }
}

/** A page → TV command for the player in picture-in-picture. `deltaMs` only for seek_by (negative is back). */
export function miniPlayerCommand(command: MiniPlayerCommandName, deltaMs?: number): string {
  return JSON.stringify({ v: SHELL_BRIDGE_VERSION, kind: 'mini_player_command', command, delta_ms: command === 'seek_by' ? Math.trunc(deltaMs ?? 0) : null })
}

const VOICE_STATES: readonly VoiceState[] = ['listening', 'partial', 'final', 'error', 'idle']
const VOICE_ERRORS: readonly VoiceError[] = ['no_match', 'unavailable', 'permission', 'busy', 'network', 'other']
/** Longer than anything a person says into a search box. */
export const MAX_VOICE_TEXT = 200

function parseVoice(m: Record<string, unknown>): ShellMessage | null {
  const state = VOICE_STATES.find((s) => s === m.state)
  if (!state) return null
  const text = m.text === null || m.text === undefined ? null : typeof m.text === 'string' ? [...m.text].map((ch) => (ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127 ? ' ' : ch)).join('').trim().slice(0, MAX_VOICE_TEXT) : undefined
  if (text === undefined) return null
  // Words are what partial and final are about: without them there is nothing to show or search for.
  if ((state === 'partial' || state === 'final') && !text) return null
  const error = state === 'error' ? (VOICE_ERRORS.find((e) => e === m.error) ?? 'other') : null
  return { kind: 'voice', state, text: state === 'partial' || state === 'final' ? text : null, error }
}

/** shell.ts dispatches this on window when the TV says its on-screen keyboard closed (nav/textEntry.ts listens). */
export const IME_HIDDEN_EVENT = 'streamhub:ime-hidden'

/** A page → TV command for voice search: start listening, or stop (nothing is searched for). */
export function voiceCommand(command: 'start' | 'stop'): string {
  return JSON.stringify({ v: SHELL_BRIDGE_VERSION, kind: 'voice_command', command })
}

/** Whether the TV's capabilities() JSON offers voice search. Anything unreadable says no. */
export function offersVoice(capabilities: unknown): boolean {
  if (typeof capabilities !== 'string') return false
  try { return (JSON.parse(capabilities) as { voice_search?: unknown } | null)?.voice_search === true } catch { return false }
}

/** At most this many keep-clear rects go to the TV (it refuses more). */
export const MAX_KEEP_CLEAR_RECTS = 8

/**
 * A page → TV report of what the picture-in-picture window should stay off, in CSS px of a page
 * `viewportWidth` wide (the TV scales to its own pixels). Rects are rounded and clipped to sane sizes.
 */
export function keepClearMessage(rects: readonly CssRect[], viewportWidth: number): string {
  const list = rects.slice(0, MAX_KEEP_CLEAR_RECTS).map((r) => ({
    x: Math.round(r.x), y: Math.round(r.y), w: Math.max(0, Math.round(r.w)), h: Math.max(0, Math.round(r.h)),
  }))
  return JSON.stringify({ v: SHELL_BRIDGE_VERSION, kind: 'keep_clear', rects: list, viewport_width: Math.round(viewportWidth) })
}

/** Do two rects overlap (touching edges do not)? */
export function overlaps(a: CssRect, b: CssRect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/**
 * How far to scroll (CSS px, positive is down) so `item` is no longer under the picture-in-picture
 * window, when the TV cannot move the window itself. Zero when they do not overlap. A window in the
 * top half pushes the item below it; one in the bottom half pulls the item above it.
 */
export function scrollClearOf(item: CssRect, pip: CssRect, viewportHeight: number, gap = 16): number {
  if (!overlaps(item, pip)) return 0
  const pipInTop = pip.y + pip.h / 2 < viewportHeight / 2
  return pipInTop ? -(pip.y + pip.h + gap - item.y) : item.y + item.h - (pip.y - gap)
}
