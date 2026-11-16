/**
 * The player's keyboard map, as data: the key handler in Player.tsx runs what this returns and the
 * shortcut sheet lists it. Arrows, Enter and Back keep their remote meaning wherever focus is being
 * moved (inside the control bar or a menu); everything here is extra for a keyboard.
 *
 * A game controller arrives here as keys too (nav/gamepadKeys.ts): X is MediaPlayPause, Y Info,
 * Start ContextMenu, the bumpers Page Down/Up, the triggers Shift+Page Down/Up and the right stick
 * AudioVolumeUp/Down.
 */
import { MID_STEP_S } from './seekSteps.ts'

export type MenuSection = 'all' | 'audio' | 'subs' | 'settings'

export type ShortcutAction =
  | { type: 'toggle-play' }
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'stop' }
  /** [stepS]: a step of its own size (the triggers' 30 s), held or not. */
  | { type: 'seek-step'; dir: -1 | 1; big: boolean; stepS?: number }
  | { type: 'channel'; dir: -1 | 1 }
  | { type: 'seek-fraction'; digit: number }
  | { type: 'seek-start' }
  | { type: 'seek-end' }
  | { type: 'speed'; dir: -1 | 1 }
  | { type: 'mute' }
  | { type: 'volume'; dir: -1 | 1 }
  | { type: 'subtitles' }
  | { type: 'menu'; section: MenuSection }
  | { type: 'fullscreen' }
  | { type: 'pip' }
  | { type: 'info' }
  | { type: 'help' }

export interface KeyLike { key: string; shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean }

export interface ShortcutContext {
  /** A live channel: nothing to seek in, no speed, and Up/Down change channel. */
  live: boolean
  /** A watch room plays at one speed for everyone. */
  room: boolean
  /** Focus is being moved with the arrows (inside the control bar): they navigate. */
  navigating: boolean
  /** Left/Right belong to the focused control (the scrubber takes them as seek steps even while navigating). */
  scrubbing?: boolean
  /** A text field has focus. */
  typing?: boolean
}

export function shortcutFor(e: KeyLike, ctx: ShortcutContext): ShortcutAction | null {
  if (ctx.typing || e.ctrlKey || e.metaKey || e.altKey) return null
  const seekable = !ctx.live
  switch (e.key) {
    case ' ': case 'k': case 'K': case 'MediaPlayPause': return { type: 'toggle-play' }
    case 'MediaPlay': return { type: 'play' }
    case 'MediaPause': return { type: 'pause' }
    case 'MediaStop': return { type: 'stop' }
    case 'j': case 'J': case 'MediaRewind': return seekable ? { type: 'seek-step', dir: -1, big: false } : null
    case 'l': case 'L': case 'MediaFastForward': return seekable ? { type: 'seek-step', dir: 1, big: false } : null
    case 'ArrowLeft': case 'ArrowRight':
      if (!seekable || (ctx.navigating && !ctx.scrubbing)) return null
      return { type: 'seek-step', dir: e.key === 'ArrowLeft' ? -1 : 1, big: !!e.shiftKey }
    case 'ArrowUp': case 'ArrowDown':
      if (ctx.live || ctx.navigating) return null
      return { type: 'volume', dir: e.key === 'ArrowUp' ? 1 : -1 }
    // Page Up/Down (a controller's bumpers): the next or previous channel on live TV, which a room
    // never changes; a 10 s step in a film, and with Shift (the triggers) 30 s. They are no focus
    // keys, so they work inside the control bar too.
    case 'PageUp': case 'PageDown': {
      const dir = e.key === 'PageUp' ? 1 : -1
      if (ctx.live) return ctx.room || e.shiftKey ? null : { type: 'channel', dir }
      return e.shiftKey ? { type: 'seek-step', dir, big: false, stepS: MID_STEP_S } : { type: 'seek-step', dir, big: false }
    }
    // The right stick. Volume belongs to each viewer, on live TV and in a room as well.
    case 'AudioVolumeUp': case 'AudioVolumeDown': return { type: 'volume', dir: e.key === 'AudioVolumeUp' ? 1 : -1 }
    case 'Home': return seekable ? { type: 'seek-start' } : null
    case 'End': return seekable ? { type: 'seek-end' } : null
    case '<': case '>': return ctx.live || ctx.room ? null : { type: 'speed', dir: e.key === '<' ? -1 : 1 }
    case 'm': case 'M': return { type: 'mute' }
    case 'c': case 'C': return { type: 'subtitles' }
    case 'a': case 'A': return { type: 'menu', section: 'audio' }
    case 's': case 'S': case 'o': case 'O': return { type: 'menu', section: 'settings' }
    case 'ContextMenu': case 'MediaTrackNext': return { type: 'menu', section: 'all' }
    case 'f': case 'F': return { type: 'fullscreen' }
    case 'p': case 'P': return { type: 'pip' }
    case 'i': case 'I': case 'Info': return { type: 'info' }
    case '?': return { type: 'help' }
  }
  // Live keeps its digits for channel numbers.
  if (seekable && /^[0-9]$/.test(e.key)) return { type: 'seek-fraction', digit: Number(e.key) }
  return null
}

export interface ShortcutLine { keys: string[]; does: string; only?: 'film' | 'live' }

/** What the shortcut sheet shows, in the order a viewer looks for it. */
export const SHORTCUT_HELP: ReadonlyArray<{ title: string; lines: ShortcutLine[] }> = [
  { title: 'Playback', lines: [
    { keys: ['Space', 'K'], does: 'Play or pause' },
    { keys: ['←', '→'], does: 'Back or forward 10 seconds. Hold to move faster.', only: 'film' },
    { keys: ['J', 'L', 'PgDn', 'PgUp'], does: 'Back or forward 10 seconds', only: 'film' },
    { keys: ['Shift PgDn', 'Shift PgUp'], does: 'Back or forward 30 seconds', only: 'film' },
    { keys: ['Shift ←', 'Shift →'], does: 'Back or forward 1 minute', only: 'film' },
    { keys: ['0', '…', '9'], does: 'Jump to 0% … 90% of the title', only: 'film' },
    { keys: ['Home', 'End'], does: 'Jump to the start or near the end', only: 'film' },
    { keys: ['Enter'], does: 'Go to the chosen position now', only: 'film' },
    { keys: ['<', '>'], does: 'Slower or faster', only: 'film' },
    { keys: ['↑', '↓', 'PgUp', 'PgDn'], does: 'Next or previous channel', only: 'live' },
    { keys: ['0', '…', '9'], does: 'Go to a channel number', only: 'live' },
    { keys: ['Enter'], does: 'Channel list', only: 'live' },
  ] },
  { title: 'Sound and picture', lines: [
    { keys: ['↑', '↓'], does: 'Volume up or down', only: 'film' },
    { keys: ['M'], does: 'Mute' },
    { keys: ['C'], does: 'Subtitles on or off' },
    { keys: ['A'], does: 'Audio tracks' },
    { keys: ['S', 'O'], does: 'Settings: speed, quality, aspect, sleep timer' },
    { keys: ['F'], does: 'Full screen' },
    { keys: ['P'], does: 'Picture in picture' },
  ] },
  { title: 'More', lines: [
    { keys: ['I'], does: 'Stream information' },
    { keys: ['?'], does: 'This list' },
    { keys: ['Esc'], does: 'Cancel a seek, close what is open, hide the controls, then leave' },
  ] },
]

