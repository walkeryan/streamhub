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
 */

export const SHELL_BRIDGE_VERSION = 1
/** The layout width the TV gets: what a 1920×1080 desktop browser lays out, not the ~960 CSS px a TV WebView reports. */
export const SHELL_WIDTH = 1920

export interface StreamHubTVBridge {
  play(json: string): boolean
  signedOut(reason: string): void
  capabilities(): string
  deliverToken(id: string, json: string): void
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
